package com.nystudios.stoneblock4bridge.bridge;

/**
 * Local transport endpoint exposed by the mod to an external AI process.
 */
public interface BridgeServer {
    void start();

    void stop();

    BridgeStatus status();

    String lastError();
}
