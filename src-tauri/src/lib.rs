use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

/// The document engine is a local Python sidecar (`python -m mdkb serve`) bound to 127.0.0.1 only.
/// Set MDKB_PYTHON to pick the interpreter, MDKB_ENGINE_DIR to point at the engine folder.
struct Engine(Mutex<Option<Child>>);

fn spawn_engine() -> Option<Child> {
    let py = std::env::var("MDKB_PYTHON").unwrap_or_else(|_| if cfg!(windows) { "python".into() } else { "python3".into() });
    let dir = std::env::var("MDKB_ENGINE_DIR").unwrap_or_else(|_| "../engine".into());
    match Command::new(py).args(["-m", "mdkb", "serve", "--port", "8765"]).current_dir(dir).spawn() {
        Ok(c) => Some(c),
        Err(e) => {
            eprintln!("could not start mdkb engine: {e}. Start it manually: (cd engine && python -m mdkb serve)");
            None
        }
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            app.manage(Engine(Mutex::new(spawn_engine())));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(state) = handle.try_state::<Engine>() {
                if let Some(mut child) = state.0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        }
    });
}
