//! Deck over iroh: reach a host's Deck by its key, with no Tailscale, port forwarding or account.
//!
//! The host side (`deck-iroh serve`) accepts iroh connections and pipes each stream to Deck's tunnel port, where every
//! request counts as remote and must come from a paired device. The phone side (the app, through JNI) listens on
//! 127.0.0.1 and pipes each TCP connection the WebView opens through one iroh connection to that host.
//! iroh finds the host by its key (n0's DNS) and connects directly when it can, through n0's relays when it can't.

use std::{collections::HashMap, net::SocketAddr, path::Path, sync::Arc};

use anyhow::{Context, Result};
use iroh::{
    Endpoint, EndpointId, SecretKey,
    endpoint::{Connection, RecvStream, SendStream, presets},
};
use tokio::{
    io::AsyncWriteExt,
    net::{TcpListener, TcpStream},
    sync::Mutex,
};

/// One bidirectional stream carries one TCP connection (an HTTP request or a WebSocket), byte for byte.
pub const ALPN: &[u8] = b"deck/tcp/1";

/// The host's key: made on first run and kept, since the phone remembers the host by it.
pub fn load_key(path: &Path) -> Result<SecretKey> {
    if let Ok(text) = std::fs::read_to_string(path) {
        return text.trim().parse().context("bad key file");
    }
    let key = SecretKey::generate();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    write_private(path, &data_encoding_hex(&key.to_bytes()))?;
    Ok(key)
}

fn data_encoding_hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

#[cfg(unix)]
fn write_private(path: &Path, text: &str) -> Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path)?;
    std::io::Write::write_all(&mut f, text.as_bytes())?;
    Ok(())
}
#[cfg(not(unix))]
fn write_private(path: &Path, text: &str) -> Result<()> {
    Ok(std::fs::write(path, text)?)
}

/// Host side: accept connections from anyone who knows the key; Deck's pairing decides who gets in.
pub async fn serve(key: SecretKey, target: SocketAddr, ready: impl FnOnce(EndpointId)) -> Result<()> {
    let endpoint = Endpoint::builder(presets::N0).secret_key(key).alpns(vec![ALPN.to_vec()]).bind().await?;
    ready(endpoint.id());
    while let Some(incoming) = endpoint.accept().await {
        tokio::spawn(async move {
            let Ok(conn) = async { incoming.accept()?.await }.await else { return };
            while let Ok((send, recv)) = conn.accept_bi().await {
                tokio::spawn(async move {
                    if let Ok(tcp) = TcpStream::connect(target).await {
                        pipe(tcp, send, recv).await;
                    }
                });
            }
        });
    }
    Ok(())
}

/// Copy both ways until each side has finished; either side failing ends both.
async fn pipe(tcp: TcpStream, mut send: SendStream, mut recv: RecvStream) {
    let _ = tcp.set_nodelay(true);
    let (mut rd, mut wr) = tcp.into_split();
    let up = async {
        let r = tokio::io::copy(&mut rd, &mut send).await;
        let _ = send.finish();
        r
    };
    let down = async {
        let r = tokio::io::copy(&mut recv, &mut wr).await;
        let _ = wr.shutdown().await;
        r
    };
    let _ = tokio::try_join!(up, down);
}

/// Phone side: one client endpoint, and per host a local port whose TCP connections go to that host.
pub struct Client {
    endpoint: Endpoint,
    ports: Mutex<HashMap<EndpointId, u16>>,
}

impl Client {
    pub async fn new() -> Result<Arc<Self>> {
        let endpoint = Endpoint::bind(presets::N0).await?;
        Ok(Arc::new(Self { endpoint, ports: Mutex::new(HashMap::new()) }))
    }

    /// The local port for this host, listening from now on. The same host gets the same port every run when it can
    /// (the page's cookies, and so its pairing, belong to 127.0.0.1:<port>).
    pub async fn open(self: &Arc<Self>, host: EndpointId) -> Result<u16> {
        let mut ports = self.ports.lock().await;
        if let Some(p) = ports.get(&host) {
            return Ok(*p);
        }
        let b = host.as_bytes();
        let preferred = 20000 + (u16::from_le_bytes([b[0], b[1]]) % 20000);
        let listener = match TcpListener::bind(("127.0.0.1", preferred)).await {
            Ok(l) => l,
            Err(_) => TcpListener::bind(("127.0.0.1", 0)).await?,
        };
        let port = listener.local_addr()?.port();
        ports.insert(host, port);
        let me = self.clone();
        tokio::spawn(async move { me.forward(host, listener).await });
        Ok(port)
    }

    async fn forward(self: Arc<Self>, host: EndpointId, listener: TcpListener) {
        let conn: Arc<Mutex<Option<Connection>>> = Arc::default();
        while let Ok((tcp, _)) = listener.accept().await {
            let (me, conn) = (self.clone(), conn.clone());
            tokio::spawn(async move {
                if let Ok((send, recv)) = me.stream(host, &conn).await {
                    pipe(tcp, send, recv).await;
                }
            });
        }
    }

    /// A new stream on the shared connection, reconnecting if it dropped (phone changed networks, host restarted).
    async fn stream(&self, host: EndpointId, conn: &Mutex<Option<Connection>>) -> Result<(SendStream, RecvStream)> {
        let mut c = conn.lock().await;
        if let Some(existing) = c.as_ref() {
            if let Ok(s) = existing.open_bi().await {
                return Ok(s);
            }
        }
        let fresh = self.endpoint.connect(host, ALPN).await?;
        let s = fresh.open_bi().await?;
        *c = Some(fresh);
        Ok(s)
    }
}

#[cfg(target_os = "android")]
mod android {
    //! `dev.deck.app.Tunnel.open(String hostKey) -> int port` (negative on failure, with the reason in logcat).
    use std::sync::{Arc, OnceLock};

    use jni::{
        JNIEnv,
        objects::{JClass, JString},
        sys::jint,
    };

    use super::Client;

    static RT: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    static CLIENT: OnceLock<Arc<Client>> = OnceLock::new();

    #[unsafe(no_mangle)]
    pub extern "system" fn Java_dev_deck_app_Tunnel_open(mut env: JNIEnv, _: JClass, key: JString) -> jint {
        let key: String = match env.get_string(&key) {
            Ok(s) => s.into(),
            Err(_) => return -1,
        };
        let rt = RT.get_or_init(|| tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap());
        let res = rt.block_on(async {
            let host = key.parse()?;
            let client = match CLIENT.get() {
                Some(c) => c.clone(),
                None => {
                    let c = Client::new().await?;
                    CLIENT.get_or_init(|| c).clone()
                }
            };
            client.open(host).await
        });
        match res {
            Ok(port) => port as jint,
            Err(e) => {
                let _ = env.throw_new("java/io/IOException", format!("{e:#}"));
                -1
            }
        }
    }
}
