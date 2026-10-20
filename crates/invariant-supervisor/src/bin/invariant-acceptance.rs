#[path = "../acceptance/mod.rs"]
mod acceptance;

fn main() {
    if let Err(error) = acceptance::main() {
        eprintln!("MVP acceptance: {error}");
        std::process::exit(1);
    }
}
