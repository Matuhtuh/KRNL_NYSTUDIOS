package com.nystudios.stoneblock4bridge.state;

import com.nystudios.stoneblock4bridge.dto.BlockObservationDto;
import com.nystudios.stoneblock4bridge.dto.EntityObservationDto;
import com.nystudios.stoneblock4bridge.dto.GameStateSnapshotDto;
import com.nystudios.stoneblock4bridge.dto.InventoryItemDto;
import com.nystudios.stoneblock4bridge.dto.InventorySnapshotDto;
import com.nystudios.stoneblock4bridge.dto.NearbyObservationDto;
import com.nystudios.stoneblock4bridge.dto.OpenScreenStateDto;
import com.nystudios.stoneblock4bridge.dto.PlayerStateDto;
import java.util.ArrayList;
import java.util.List;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.MobCategory;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.item.ItemStack;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.world.level.block.state.BlockState;

/**
 * Captures real client state when available; returns typed partial snapshots otherwise.
 */
public final class ClientStateProvider implements StateProvider {
    private static final int DEFAULT_RADIUS = 4;

    @Override
    public PlayerStateDto getCurrentPlayerState(long tick) {
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        ClientLevel level = mc.level;
        if (player == null || level == null) {
            return new PlayerStateDto(
                    tick,
                    "unknown",
                    0.0,
                    0.0,
                    0.0,
                    0.0f,
                    0.0f,
                    0.0f,
                    20,
                    false,
                    false,
                    "minecraft:air",
                    "minecraft:air",
                    0
            );
        }

        return new PlayerStateDto(
                tick,
                level.dimension().location().toString(),
                player.getX(),
                player.getY(),
                player.getZ(),
                player.getYRot(),
                player.getXRot(),
                player.getHealth(),
                player.getFoodData().getFoodLevel(),
                player.onGround(),
                player.isInWaterOrBubble(),
                idForItem(player.getMainHandItem()),
                idForItem(player.getOffhandItem()),
                player.getInventory().selected
        );
    }

    @Override
    public InventorySnapshotDto getInventoryContents() {
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        if (player == null) {
            return new InventorySnapshotDto(List.of(), List.of(), true, "Player unavailable on client");
        }

        List<InventoryItemDto> allItems = new ArrayList<>();
        int size = player.getInventory().getContainerSize();
        for (int slot = 0; slot < size; slot++) {
            ItemStack stack = player.getInventory().getItem(slot);
            allItems.add(new InventoryItemDto(slot, idForItem(stack), stack.getCount(), stack.isEmpty()));
        }

        List<InventoryItemDto> hotbar = new ArrayList<>();
        for (int slot = 0; slot < 9; slot++) {
            ItemStack stack = player.getInventory().getItem(slot);
            hotbar.add(new InventoryItemDto(slot, idForItem(stack), stack.getCount(), stack.isEmpty()));
        }

        return new InventorySnapshotDto(allItems, hotbar, false, null);
    }

    @Override
    public NearbyObservationDto getNearbyObservation(int radius) {
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        ClientLevel level = mc.level;
        if (player == null || level == null) {
            return new NearbyObservationDto(List.of(), List.of(), radius);
        }

        List<BlockObservationDto> blocks = sampleNearbyBlocks(level, player.blockPosition(), radius);
        List<EntityObservationDto> entities = sampleNearbyEntities(level, player.getX(), player.getY(), player.getZ(), radius + 4);
        return new NearbyObservationDto(blocks, entities, radius);
    }

    @Override
    public OpenScreenStateDto getOpenScreenState() {
        Minecraft mc = Minecraft.getInstance();
        Screen screen = mc.screen;
        if (screen == null) {
            return new OpenScreenStateDto(false, "none", "No screen", 0);
        }

        int slotCount = 0;
        if (screen instanceof AbstractContainerScreen<?> containerScreen) {
            slotCount = containerScreen.getMenu().slots.size();
        }
        return new OpenScreenStateDto(true, screen.getClass().getSimpleName(), screen.getTitle().getString(), slotCount);
    }

    @Override
    public GameStateSnapshotDto getFullStateSnapshot(long tick) {
        int radius = DEFAULT_RADIUS;
        PlayerStateDto player = getCurrentPlayerState(tick);
        InventorySnapshotDto inventory = getInventoryContents();
        NearbyObservationDto nearby = getNearbyObservation(radius);
        OpenScreenStateDto screen = getOpenScreenState();

        List<String> warnings = new ArrayList<>();
        boolean partial = false;
        if ("unknown".equals(player.dimension())) {
            partial = true;
            warnings.add("Player or world unavailable while capturing snapshot");
        }
        if (inventory.partial()) {
            partial = true;
            warnings.add(inventory.note() == null ? "Inventory is partial" : inventory.note());
        }

        return new GameStateSnapshotDto(
                player,
                inventory,
                nearby.blocks(),
                nearby.entities(),
                screen,
                radius,
                partial,
                warnings
        );
    }

    private static List<BlockObservationDto> sampleNearbyBlocks(ClientLevel level, BlockPos center, int radius) {
        List<BlockObservationDto> result = new ArrayList<>();
        BlockPos min = center.offset(-radius, -2, -radius);
        BlockPos max = center.offset(radius, 2, radius);
        int count = 0;
        for (BlockPos pos : BlockPos.betweenClosed(min, max)) {
            BlockState state = level.getBlockState(pos);
            String blockId = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
            result.add(new BlockObservationDto(
                    blockId,
                    pos.getX(),
                    pos.getY(),
                    pos.getZ(),
                    state.canBeReplaced(),
                    state.getDestroySpeed(level, pos)
            ));
            count++;
            if (count >= 256) {
                break;
            }
        }
        return result;
    }

    private static List<EntityObservationDto> sampleNearbyEntities(ClientLevel level, double x, double y, double z, int radius) {
        List<EntityObservationDto> result = new ArrayList<>();
        for (Entity entity : level.entitiesForRendering()) {
            if (entity.distanceToSqr(x, y, z) > (double) radius * radius) {
                continue;
            }
            boolean hostile = entity instanceof Enemy || entity.getType().getCategory() == MobCategory.MONSTER;
            float health = entity instanceof net.minecraft.world.entity.LivingEntity living ? living.getHealth() : 0.0f;
            result.add(new EntityObservationDto(
                    BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString(),
                    entity.getName().getString(),
                    entity.getX(),
                    entity.getY(),
                    entity.getZ(),
                    health,
                    hostile
            ));
            if (result.size() >= 64) {
                break;
            }
        }
        return result;
    }

    private static String idForItem(ItemStack stack) {
        if (stack == null || stack.isEmpty()) {
            return "minecraft:air";
        }
        return BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
    }
}
