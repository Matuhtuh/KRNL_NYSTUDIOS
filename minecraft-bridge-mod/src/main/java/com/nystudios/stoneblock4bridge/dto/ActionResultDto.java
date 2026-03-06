package com.nystudios.stoneblock4bridge.dto;

/**
 * Result payload for each attempted action.
 */
public record ActionResultDto(
        String requestId,
        boolean accepted,
        boolean completed,
        String message
) {
}
