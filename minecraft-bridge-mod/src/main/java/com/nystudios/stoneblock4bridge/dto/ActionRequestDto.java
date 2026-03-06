package com.nystudios.stoneblock4bridge.dto;

import java.util.Map;

/**
 * External command request sent to the bridge mod. Commands are deterministic and bounded.
 */
public record ActionRequestDto(
        String requestId,
        String actionType,
        Map<String, String> parameters,
        int timeoutTicks
) {
}
