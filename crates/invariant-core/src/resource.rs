use std::fmt;
use std::net::SocketAddr;

use serde::Serialize;

use crate::{BrokerSessionId, ExecutionId, PrincipalId, ResourceId};

/// Established by the supervisor and attached to a channel on the host.
/// Deliberately cannot be deserialized from a workload message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionContext {
    /// Host principal that owns the execution.
    pub principal_id: PrincipalId,
    /// Fresh identity of the execution bound to the workload channel.
    pub execution_id: ExecutionId,
    /// Fresh identity of the broker session serving this execution.
    pub broker_session_id: BrokerSessionId,
    /// SHA-256 of the initial artifact, computed by trusted host code.
    pub initial_artifact_digest: [u8; 32],
}

/// The MVP only supports plain HTTP to a configured mock IP and port.
/// No workload-supplied hostname, URL, or DNS lookup is needed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct MockHttpOrigin(pub SocketAddr);

impl fmt::Display for MockHttpOrigin {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "http://{}", self.0)
    }
}

/// An operator-configured resource snapshot. A grant retains its own copy.
/// The broker must freeze configuration for its session and compare identities
/// in full, not just `resource_id`, before admission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ResourceIdentity {
    /// Host-configured resource label.
    pub resource_id: ResourceId,
    /// Version of this exact resource configuration.
    pub configuration_version: u64,
    /// Configured mock HTTP address; never supplied by a workload.
    pub origin: MockHttpOrigin,
    /// Account associated with the resource.
    pub account: String,
    /// Environment associated with the resource.
    pub environment: String,
    /// Version of the trusted resource adapter.
    pub adapter_version: String,
}
