use std::fmt;
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

use serde::Serialize;

/// Maximum decoded HTTP response body size.
pub const MAX_RESPONSE_BYTES: usize = 1_048_576;
/// Maximum HTTP response header size, including its terminator.
pub const MAX_HEADER_BYTES: usize = 32 * 1024;
/// Total HTTP connection, write and read deadline for one admitted attempt.
pub const TARGET_TIMEOUT: Duration = Duration::from_secs(5);

/// A bounded HTTP response; Debug exposes the body length, not its contents.
#[derive(Clone, PartialEq, Eq)]
pub struct HttpResponse {
    /// Observed status, including error and redirect responses.
    pub status: u16,
    /// Decoded response bytes, bounded by [`MAX_RESPONSE_BYTES`].
    pub body: Vec<u8>,
}

impl fmt::Debug for HttpResponse {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("HttpResponse")
            .field("status", &self.status)
            .field("body_bytes", &self.body.len())
            .finish()
    }
}

/// Failure of an already charged HTTP attempt; no automatic retry is made.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UpstreamError {
    /// Connection or I/O failed, including unexpected end of a response body.
    Transport,
    /// The total target deadline expired.
    Timeout,
    /// HTTP status, headers or framing were malformed or incomplete.
    InvalidResponse,
    /// Response headers exceeded [`MAX_HEADER_BYTES`].
    HeadersTooLarge,
    /// The body exceeded [`MAX_RESPONSE_BYTES`].
    BodyTooLarge,
    /// A nonidentity content encoding was returned.
    UnsupportedEncoding,
    /// Unsupported transfer framing, such as chunked encoding, was returned.
    UnsupportedTransferEncoding,
}

fn remaining(deadline: Instant) -> Result<Duration, UpstreamError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|duration| !duration.is_zero())
        .ok_or(UpstreamError::Timeout)
}

fn io_error(error: io::Error) -> UpstreamError {
    match error.kind() {
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => UpstreamError::Timeout,
        _ => UpstreamError::Transport,
    }
}

fn read(
    stream: &mut TcpStream,
    buffer: &mut [u8],
    deadline: Instant,
) -> Result<usize, UpstreamError> {
    loop {
        stream
            .set_read_timeout(Some(remaining(deadline)?))
            .map_err(io_error)?;
        match stream.read(buffer) {
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            result => return result.map_err(io_error),
        }
    }
}

/// One TCP connection, one fixed GET, no proxy, DNS, redirect following,
/// connection pooling, or HTTP retries. Only identity Content-Length or
/// connection-close framing is supported by this deliberately narrow adapter.
pub(crate) fn get_invoices(address: SocketAddr) -> Result<HttpResponse, UpstreamError> {
    let deadline = Instant::now() + TARGET_TIMEOUT;
    let mut stream =
        TcpStream::connect_timeout(&address, remaining(deadline)?).map_err(io_error)?;
    let request = format!(
        "GET /invoices HTTP/1.1\r\nHost: {address}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
    );
    let mut bytes = request.as_bytes();
    while !bytes.is_empty() {
        stream
            .set_write_timeout(Some(remaining(deadline)?))
            .map_err(io_error)?;
        match stream.write(bytes) {
            Ok(0) => return Err(UpstreamError::Transport),
            Ok(count) => bytes = &bytes[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(io_error(error)),
        }
    }
    let mut buffer = Vec::with_capacity(4096);
    let mut chunk = [0; 4096];
    let header_end = loop {
        if let Some(position) = buffer.windows(4).position(|bytes| bytes == b"\r\n\r\n") {
            break position + 4;
        }
        let available = MAX_HEADER_BYTES
            .saturating_sub(buffer.len())
            .min(chunk.len());
        if available == 0 {
            return Err(UpstreamError::HeadersTooLarge);
        }
        let count = read(&mut stream, &mut chunk[..available], deadline)?;
        if count == 0 {
            return Err(UpstreamError::InvalidResponse);
        }
        buffer.extend_from_slice(&chunk[..count]);
    };
    let (status, content_length) = parse_headers(&buffer[..header_end])?;
    let mut body = buffer[header_end..].to_vec();
    if body.len() > MAX_RESPONSE_BYTES {
        return Err(UpstreamError::BodyTooLarge);
    }
    if content_length.is_some_and(|length| body.len() > length) {
        return Err(UpstreamError::InvalidResponse);
    }
    loop {
        if content_length == Some(body.len()) {
            break;
        }
        // Read at most one byte beyond the cap to detect an oversized body.
        let allowed = content_length
            .unwrap_or(MAX_RESPONSE_BYTES + 1)
            .saturating_sub(body.len())
            .min(chunk.len());
        if allowed == 0 {
            return Err(UpstreamError::BodyTooLarge);
        }
        let count = read(&mut stream, &mut chunk[..allowed], deadline)?;
        if count == 0 {
            if content_length.is_some_and(|length| body.len() != length) {
                return Err(UpstreamError::InvalidResponse);
            }
            break;
        }
        if body.len() + count > MAX_RESPONSE_BYTES {
            return Err(UpstreamError::BodyTooLarge);
        }
        body.extend_from_slice(&chunk[..count]);
    }
    Ok(HttpResponse { status, body })
}

fn parse_headers(bytes: &[u8]) -> Result<(u16, Option<usize>), UpstreamError> {
    let mut headers = [httparse::EMPTY_HEADER; 128];
    let mut response = httparse::Response::new(&mut headers);
    if !response
        .parse(bytes)
        .map_err(|_| UpstreamError::InvalidResponse)?
        .is_complete()
    {
        return Err(UpstreamError::InvalidResponse);
    }
    let status = response
        .code
        .filter(|code| (200..=599).contains(code))
        .ok_or(UpstreamError::InvalidResponse)?;
    let mut length = None;
    for header in response.headers.iter() {
        if header.name.eq_ignore_ascii_case("transfer-encoding") {
            return Err(UpstreamError::UnsupportedTransferEncoding);
        }
        if header.name.eq_ignore_ascii_case("upgrade") {
            return Err(UpstreamError::InvalidResponse);
        }
        if header.name.eq_ignore_ascii_case("content-encoding")
            && !header.value.eq_ignore_ascii_case(b"identity")
        {
            return Err(UpstreamError::UnsupportedEncoding);
        }
        if header.name.eq_ignore_ascii_case("content-length") {
            let text =
                std::str::from_utf8(header.value).map_err(|_| UpstreamError::InvalidResponse)?;
            if length.is_some()
                || text.is_empty()
                || !text.bytes().all(|byte| byte.is_ascii_digit())
            {
                return Err(UpstreamError::InvalidResponse);
            }
            let value = text
                .parse::<usize>()
                .map_err(|_| UpstreamError::InvalidResponse)?;
            if value > MAX_RESPONSE_BYTES {
                return Err(UpstreamError::BodyTooLarge);
            }
            length = Some(value);
        }
    }
    if status == 204 || status == 304 {
        length = Some(0);
    }
    Ok((status, length))
}
