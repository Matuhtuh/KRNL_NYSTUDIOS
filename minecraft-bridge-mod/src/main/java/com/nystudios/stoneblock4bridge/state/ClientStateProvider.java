package com.nystudios.stoneblock4bridge.state;

import com.nystudios.stoneblock4bridge.dto.BlockObservationDto;
import com.nystudios.stoneblock4bridge.dto.GameStateSnapshotDto;
import com.nystudios.stoneblock4bridge.dto.InventoryItemDto;
import com.nystudios.stoneblock4bridge.dto.InventorySnapshotDto;
import com.nystudios.stoneblock4bridge.dto.NearbyObservationDto;
import com.nystudios.stoneblock4bridge.dto.PlayerStateDto;
import com.nystudios.stoneblock4bridge.screen.ClientScreenInspector;
import com.nystudios.stoneblock4bridge.screen.ScreenInspector;
import java.util.List;

/**
 * Client-side state provider with explicit placeholders.
 */
public final class ClientStateProvider implements StateProvider {
    private final ScreenInspector screenInspector = new ClientScreenInspector();

    @Override
    public PlayerStateDto getCurrentPlayerState() {
        return new PlayerStateDto(
                0L,
                "minecraft:overworld",
                0.0,
                64.0,
                0.0,
                0.0f,
                0.0f,
                20.0f,
                20,
                true,
                false,
                "minecraft:air",
                "minecraft:air"
        );
    }

    @Override
    public InventorySnapshotDto getInventoryContents() {
        return new InventorySnapshotDto(List.of(new InventoryItemDto(0, "minecraft:air", 0, true)));
    }

    @Override
    public NearbyObservationDto getNearbyObservation() {
        return new NearbyObservationDto(
                List.of(new BlockObservationDto("minecraft:stone", 1, 64, 0, false, 1.5f)),
                List.of(),
                6
        );
    }

    @Override
    public GameStateSnapshotDto getFullStateSnapshot(long tick) {
        PlayerStateDto player = getCurrentPlayerState();
        NearbyObservationDto nearby = getNearbyObservation();
        return new GameStateSnapshotDto(
                new PlayerStateDto(
                        tick,
                        player.dimension(),
                        player.x(),
                        player.y(),
                        player.z(),
                        player.yaw(),
                        player.pitch(),
                        player.health(),
                        player.hunger(),
                        player.onGround(),
                        player.inFluid(),
                        player.heldMainHandItem(),
                        player.heldOffHandItem()
                ),
                getInventoryContents(),
                nearby.blocks(),
                nearby.entities(),
                screenInspector.inspectCurrentScreen()
        );
    }
}
