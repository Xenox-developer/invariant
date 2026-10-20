use std::io::{self, Read};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::{Duration, Instant};

pub enum Event {
    Data { stdout: bool, bytes: Vec<u8> },
    Closed,
    Failed,
}

/// A bounded queue applies backpressure instead of buffering arbitrary output.
pub fn capture(command: &mut Command) -> io::Result<(Child, Receiver<Event>)> {
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let (sender, receiver) = mpsc::sync_channel(8);
    let pipes: Vec<(bool, Box<dyn Read + Send>)> = vec![
        (true, Box::new(child.stdout.take().unwrap())),
        (false, Box::new(child.stderr.take().unwrap())),
    ];
    for (stdout, mut pipe) in pipes {
        let sender = sender.clone();
        thread::spawn(move || {
            let mut buffer = [0; 4096];
            loop {
                match pipe.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(count) => {
                        if sender
                            .send(Event::Data {
                                stdout,
                                bytes: buffer[..count].to_vec(),
                            })
                            .is_err()
                        {
                            return;
                        }
                    }
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    Err(_) => {
                        let _ = sender.send(Event::Failed);
                        break;
                    }
                }
            }
            let _ = sender.send(Event::Closed);
        });
    }
    Ok((child, receiver))
}

pub fn bounded(command: &mut Command, limit: usize, timeout: Duration) -> io::Result<Vec<u8>> {
    let (mut child, receiver) = capture(command.stdin(Stdio::null()))?;
    let result = (|| {
        let deadline = Instant::now() + timeout;
        let mut bytes = Vec::new();
        let mut closed = 0;
        let mut status = None;
        loop {
            if Instant::now() >= deadline {
                return Err(io::Error::other("runtime command timed out"));
            }
            match receiver.recv_timeout(Duration::from_millis(10)) {
                Ok(Event::Data { bytes: chunk, .. }) => {
                    if bytes.len() + chunk.len() > limit {
                        return Err(io::Error::other("runtime command output exceeded limit"));
                    }
                    bytes.extend(chunk);
                }
                Ok(Event::Closed) => closed += 1,
                Ok(Event::Failed) => return Err(io::Error::other("runtime output unavailable")),
                Err(_) => {}
            }
            if status.is_none() {
                status = child.try_wait()?;
            }
            if closed == 2
                && let Some(status) = status
            {
                return if status.success() {
                    Ok(bytes)
                } else {
                    Err(io::Error::other("runtime command failed"))
                };
            }
        }
    })();
    if result.is_err() {
        let _ = child.kill();
    }
    let _ = child.wait();
    result
}

/// Escape controls (including bidi controls), invalid UTF-8 and ANSI/OSC bytes.
pub fn inert(bytes: &[u8]) -> String {
    let mut result = String::new();
    for c in String::from_utf8_lossy(bytes).chars() {
        if (c.is_control() && c != '\n')
            || matches!(c, '\u{061c}' | '\u{200b}'..='\u{200f}' | '\u{2028}'..='\u{202e}' | '\u{2060}'..='\u{206f}' | '\u{feff}')
        {
            result.extend(c.escape_unicode());
        } else {
            result.push(c);
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn output_is_inert() {
        let output = inert(b"hello\n\x1b]52;clip\x07\r\x08\xff");
        assert!(output.starts_with("hello\n\\u{1b}]52;clip\\u{7}\\u{d}\\u{8}"));
        assert!(!output.contains('\x1b'));
        assert_eq!(inert("a\u{202e}b".as_bytes()), "a\\u{202e}b");
    }
    #[test]
    fn excessive_command_output_and_hung_commands_are_bounded() {
        assert!(
            bounded(
                Command::new("sh").args(["-c", "while :; do echo overflow; done"]),
                100,
                Duration::from_secs(2)
            )
            .is_err()
        );
        let start = Instant::now();
        assert!(
            bounded(
                Command::new("sleep").arg("5"),
                100,
                Duration::from_millis(100)
            )
            .is_err()
        );
        assert!(start.elapsed() < Duration::from_secs(2));
    }
}
