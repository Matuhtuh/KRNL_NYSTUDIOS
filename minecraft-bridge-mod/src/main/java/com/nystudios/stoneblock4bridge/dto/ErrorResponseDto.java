package com.nystudios.stoneblock4bridge.dto;

/**
 * Structured error response for malformed bridge requests.
 */
public record ErrorResponseDto(
        String errorCode,
        String message
) {
}
