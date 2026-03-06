package com.nystudios.stoneblock4bridge.dto;

/**
 * Snapshot of current player state needed by an external StoneBlock 4 AI planner.
 */
public record PlayerStateDto(
        long tick,
        String dimension,
        double x,
        double y,
        double z,
        float yaw,
        float pitch,
        float health,
        int hunger,
        boolean onGround,
        boolean inFluid,
        String heldMainHandItem,
        String heldOffHandItem
) {
}
