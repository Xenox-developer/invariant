use serde::Serialize;

// Distinct ID types prevent accidentally using a grant ID as an execution ID.
// These constructors label trusted state; they do not generate unique IDs or
// make the caller an authority issuer. The supervisor must supply fresh IDs.
macro_rules! identifier {
    ($($name:ident),+ $(,)?) => {$(
        #[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize)]
        #[serde(transparent)]
        /// A typed label supplied by trusted host code, not a bearer credential.
        pub struct $name(String);

        impl $name {
            /// Wrap a trusted label without generating or validating its identity.
            pub fn new(value: impl Into<String>) -> Self {
                Self(value.into())
            }

            /// Return the original label without normalization.
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }
    )+};
}

identifier!(
    PrincipalId,
    ExecutionId,
    BrokerSessionId,
    ResourceId,
    GrantId
);
