#![doc = include_str!("../README.md")]
#![deny(missing_docs)]

mod decision;
mod grant;
mod identity;
mod permissions;
mod request;
mod resource;

pub use decision::{Decision, DenialReason};
pub use grant::{
    DeadlineOverflow, Grant, GrantState, MVP_GRANT_LIFETIME, MVP_REQUEST_BUDGET, OperationScope,
};
pub use identity::{BrokerSessionId, ExecutionId, GrantId, PrincipalId, ResourceId};
pub use permissions::{AuthorizationState, CapabilityBinding, ExecutionState, check_permission};
pub use request::{EffectRequest, InvalidRequest, MAX_REQUEST_FRAME_BYTES};
pub use resource::{ExecutionContext, MockHttpOrigin, ResourceIdentity};
