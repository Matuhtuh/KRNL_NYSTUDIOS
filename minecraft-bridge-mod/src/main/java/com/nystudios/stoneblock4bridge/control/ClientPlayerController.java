package com.nystudios.stoneblock4bridge.control;

import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import java.util.List;
import java.util.Map;

/**
 * Placeholder implementation for future input-to-Minecraft wiring.
 */
public final class ClientPlayerController implements PlayerController {
    @Override
    public ActionResultDto performMovementInput(String requestId, Map<String, String> parameters) {
        return new ActionResultDto(
                requestId,
                true,
                false,
                false,
                "partial_placeholder",
                "Movement input wiring remains partial",
                List.of("client_loaded"),
                List.of("no_observable_state_change")
        );
    }

    @Override
    public ActionResultDto interactWithBlock(String requestId, Map<String, String> parameters) {
        return new ActionResultDto(
                requestId,
                true,
                false,
                false,
                "partial_placeholder",
                "Block interaction wiring remains partial",
                List.of("reach_target_visible"),
                List.of("no_observable_state_change")
        );
    }

    @Override
    public ActionResultDto stopAllInputs(String requestId) {
        return new ActionResultDto(
                requestId,
                true,
                true,
                true,
                null,
                "Input reset acknowledged",
                List.of("client_loaded"),
                List.of("state_changed")
        );
    }
}
