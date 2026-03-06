package com.nystudios.stoneblock4bridge.dto;

/**
 * Heartbeat payload used by external brain bootstrapping and health checks.
 */
public record HeartbeatResponseDto(
        String status,
        String protocolVersion,
        String bridgeMode,
        String bridgeStatus,
        String detail
) {
}
