//! deck-iroh serve [--key FILE] [--to ADDR] [--id-file FILE]: put this host's Deck on iroh (prints its key).
//! deck-iroh connect HOSTKEY: listen on 127.0.0.1 and forward to that host (testing; the app does this itself).

use std::path::PathBuf;

use anyhow::{Context, Result, bail};
use deck_tunnel::{Client, load_key, serve};

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let opt = |name: &str| args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned();
    let config = PathBuf::from(std::env::var("HOME").context("HOME")?).join(".config/deck");
    match args.first().map(String::as_str) {
        Some("serve") => {
            let key = load_key(&opt("--key").map(PathBuf::from).unwrap_or(config.join("iroh.key")))?;
            let to = opt("--to").unwrap_or("127.0.0.1:8788".into()).parse()?;
            let id_file = opt("--id-file").map(PathBuf::from).unwrap_or(config.join("iroh.id"));
            serve(key, to, |id| {
                println!("{id}");
                let _ = std::fs::write(&id_file, format!("{id}\n"));
            })
            .await
        }
        Some("connect") => {
            let host = args.get(1).context("connect HOSTKEY")?.parse()?;
            let port = Client::new().await?.open(host).await?;
            println!("http://127.0.0.1:{port}/");
            std::future::pending().await
        }
        _ => bail!("usage: deck-iroh serve [--key FILE] [--to ADDR] [--id-file FILE] | connect HOSTKEY"),
    }
}
