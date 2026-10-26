package dev.deck.app;

// JNI into libdeck_tunnel.so (tunnel/src/lib.rs): the local port that forwards to the host with this iroh key.
final class Tunnel {
    static { System.loadLibrary("deck_tunnel"); }
    static native int open(String hostKey) throws java.io.IOException;
}
