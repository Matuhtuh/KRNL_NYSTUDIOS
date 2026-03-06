package com.nystudios.stoneblock4bridge.action;

import com.nystudios.stoneblock4bridge.dto.ActionRequestDto;
import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import java.util.List;
import java.util.Map;
import java.util.Set;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Deterministic action executor with small real action surface for local debugging.
 */
public final class ClientActionExecutor implements ActionExecutor {
    private static final Logger LOGGER = LoggerFactory.getLogger(ClientActionExecutor.class);
    private static final Set<String> SUPPORTED_ACTION_TYPES = Set.of(
            "noop",
            "select_hotbar_slot",
            "turn_to_yaw_pitch",
            "move_forward_short",
            "interact_use",
            "inventory_click",
            "mine_block",
            "place_block"
    );

    @Override
    public ActionResultDto performAction(ActionRequestDto request) {
        if (request.actionType() == null || request.requestId() == null) {
            return result("unknown", false, false, false, "bad_request", "Missing request_id or action_type", List.of(), List.of());
        }

        if (!SUPPORTED_ACTION_TYPES.contains(request.actionType())) {
            return result(
                    request.requestId(),
                    false,
                    false,
                    false,
                    "unsupported_action",
                    "Unsupported action type",
                    List.of("supported_action_required"),
                    List.of()
            );
        }

        LocalPlayer player = Minecraft.getInstance().player;
        if (player == null) {
            return result(
                    request.requestId(),
                    false,
                    false,
                    false,
                    "player_unavailable",
                    "Minecraft player is not available on client",
                    List.of("player_present"),
                    List.of()
            );
        }

        LOGGER.info("bridge action {} params={}", request.actionType(), request.parameters());
        return switch (request.actionType()) {
            case "noop" -> result(request.requestId(), true, true, true, null, "Noop acknowledged", List.of(), List.of("state_changed"));
            case "select_hotbar_slot" -> handleSelectHotbar(request.requestId(), player, request.parameters());
            case "turn_to_yaw_pitch" -> handleTurn(request.requestId(), player, request.parameters());
            case "move_forward_short" -> handleMoveForwardShort(request.requestId(), request.parameters());
            case "interact_use" -> handleInteractUse(request.requestId(), request.parameters());
            case "inventory_click" -> result(
                    request.requestId(),
                    true,
                    false,
                    false,
                    "partial_placeholder",
                    "Inventory click remains typed placeholder until robust menu wiring is added",
                    List.of("screen_open"),
                    List.of("no_observable_state_change")
            );
            case "mine_block", "place_block" -> result(
                    request.requestId(),
                    true,
                    false,
                    false,
                    "partial_placeholder",
                    "Mine/place remain typed placeholders pending reliable reach/raycast/timing controls",
                    List.of("tool_or_block_available"),
                    List.of("no_observable_state_change")
            );
            default -> result(request.requestId(), false, false, false, "unsupported_action", "Unsupported action type", List.of(), List.of());
        };
    }

    private static ActionResultDto handleSelectHotbar(String requestId, LocalPlayer player, Map<String, Object> parameters) {
        int slot = asInt(parameters.get("slot"), -1);
        if (slot < 0 || slot > 8) {
            return result(requestId, false, false, false, "bad_parameter", "slot must be between 0 and 8", List.of("slot_range"), List.of());
        }
        player.getInventory().selected = slot;
        return result(requestId, true, true, true, null, "Selected hotbar slot", List.of("slot_range"), List.of("state_changed"));
    }

    private static ActionResultDto handleTurn(String requestId, LocalPlayer player, Map<String, Object> parameters) {
        float yaw = asFloat(parameters.get("yaw"), player.getYRot());
        float pitch = asFloat(parameters.get("pitch"), player.getXRot());
        pitch = Math.max(-90.0f, Math.min(90.0f, pitch));
        player.setYRot(yaw);
        player.setXRot(pitch);
        return result(requestId, true, true, true, null, "Applied yaw/pitch", List.of("yaw_pitch_valid"), List.of("state_changed"));
    }

    private static ActionResultDto handleMoveForwardShort(String requestId, Map<String, Object> parameters) {
        int ticks = asInt(parameters.get("ticks"), 4);
        Minecraft minecraft = Minecraft.getInstance();
        minecraft.options.keyUp.setDown(true);
        new Thread(() -> {
            try {
                Thread.sleep(Math.max(1, ticks) * 50L);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            minecraft.execute(() -> minecraft.options.keyUp.setDown(false));
        }, "stoneblock4bridge-move-forward").start();
        return result(requestId, true, true, true, null, "Forward input pulse issued", List.of("client_input_ready"), List.of("state_changed"));
    }

    private static ActionResultDto handleInteractUse(String requestId, Map<String, Object> parameters) {
        int ticks = asInt(parameters.get("ticks"), 2);
        Minecraft minecraft = Minecraft.getInstance();
        minecraft.options.keyUse.setDown(true);
        new Thread(() -> {
            try {
                Thread.sleep(Math.max(1, ticks) * 50L);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            minecraft.execute(() -> minecraft.options.keyUse.setDown(false));
        }, "stoneblock4bridge-use").start();
        return result(requestId, true, true, true, null, "Use/interact pulse issued", List.of("client_input_ready"), List.of("state_changed"));
    }

    private static int asInt(Object value, int fallback) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        return fallback;
    }

    private static float asFloat(Object value, float fallback) {
        if (value instanceof Number number) {
            return number.floatValue();
        }
        return fallback;
    }

    private static ActionResultDto result(
            String requestId,
            boolean accepted,
            boolean completed,
            boolean success,
            String errorCode,
            String message,
            List<String> preconditions,
            List<String> postconditions
    ) {
        return new ActionResultDto(requestId, accepted, completed, success, errorCode, message, preconditions, postconditions);
    }
}
