use std::fmt;

use serde::Deserialize;

/// Maximum UTF-8 JSON request payload, excluding transport framing.
pub const MAX_REQUEST_FRAME_BYTES: usize = 16 * 1024;

/// Untrusted intent. Parsing establishes syntax and size limits, not authority.
/// A forged capability or forbidden operation can parse successfully and must
/// still be rejected by [`crate::check_permission`].
#[derive(PartialEq, Eq)]
pub struct EffectRequest {
    request_id: String,
    capability: String,
    method: String,
    path: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireEffectRequest {
    request_id: String,
    capability: String,
    method: String,
    path: String,
}

impl EffectRequest {
    /// The transport must also cap its frame before buffering it in memory.
    /// String lengths below are in UTF-8 bytes. No normalization is performed:
    /// [`crate::check_permission`] requires exactly `GET` and `/invoices`.
    pub fn from_json(frame: &[u8]) -> Result<Self, InvalidRequest> {
        if frame.len() > MAX_REQUEST_FRAME_BYTES {
            return Err(InvalidRequest::FrameTooLarge);
        }
        let wire: WireEffectRequest =
            serde_json::from_slice(frame).map_err(|_| InvalidRequest::Malformed)?;
        for (field, value, maximum) in [
            ("request_id", wire.request_id.as_str(), 128),
            ("capability", wire.capability.as_str(), 256),
            ("method", wire.method.as_str(), 16),
            ("path", wire.path.as_str(), 2_048),
        ] {
            if value.is_empty() || value.len() > maximum {
                return Err(InvalidRequest::InvalidField(field));
            }
        }
        Ok(Self {
            request_id: wire.request_id,
            capability: wire.capability,
            method: wire.method,
            path: wire.path,
        })
    }

    /// Workload-provided request ID, scoped to the execution by the broker.
    pub fn request_id(&self) -> &str {
        &self.request_id
    }

    /// Unverified capability handle; callers must avoid logging this value.
    pub fn capability(&self) -> &str {
        &self.capability
    }

    /// Original method, without case folding or normalization.
    pub fn method(&self) -> &str {
        &self.method
    }

    /// Original path, without decoding or normalization.
    pub fn path(&self) -> &str {
        &self.path
    }
}

impl fmt::Debug for EffectRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("EffectRequest")
            .field("request_id", &self.request_id)
            .field("capability", &"[redacted]")
            .field("method", &self.method)
            .field("path", &self.path)
            .finish()
    }
}

/// A request failed structural or size validation before authorization.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InvalidRequest {
    /// The JSON payload exceeds [`MAX_REQUEST_FRAME_BYTES`].
    FrameTooLarge,
    /// Invalid JSON, duplicate/unknown fields or an incompatible structure.
    Malformed,
    /// The named field is empty or exceeds its UTF-8 byte limit.
    InvalidField(&'static str),
}

impl fmt::Display for InvalidRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::FrameTooLarge => f.write_str("request frame exceeds 16 KiB"),
            Self::Malformed => f.write_str("invalid request structure"),
            Self::InvalidField(field) => write!(f, "invalid length for {field}"),
        }
    }
}

impl std::error::Error for InvalidRequest {}
