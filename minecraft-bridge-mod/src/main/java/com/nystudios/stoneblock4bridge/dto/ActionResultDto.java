package com.nystudios.stoneblock4bridge.dto;

import java.util.List;

/**
 * Result payload for each attempted action.
 */
public record ActionResultDto(
        String requestId,
        boolean accepted,
        boolean completed,
        boolean success,
        String errorCode,
        String message,
        List<String> preconditions,
        List<String> postconditions
) {
}
