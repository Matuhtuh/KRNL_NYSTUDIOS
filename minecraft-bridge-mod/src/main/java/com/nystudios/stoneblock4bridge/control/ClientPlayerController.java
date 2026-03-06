package com.nystudios.stoneblock4bridge.control;

import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import java.util.Map;

/**
 * Placeholder implementation for future input-to-Minecraft wiring.
 */
public final class ClientPlayerController implements PlayerController {
    @Override
    public ActionResultDto performMovementInput(String requestId, Map<String, String> parameters) {
        return new ActionResultDto(requestId, false, false, "Movement input wiring is not implemented yet");
    }

    @Override
    public ActionResultDto interactWithBlock(String requestId, Map<String, String> parameters) {
        return new ActionResultDto(requestId, false, false, "Block interaction wiring is not implemented yet");
    }

    @Override
    public ActionResultDto stopAllInputs(String requestId) {
        return new ActionResultDto(requestId, false, false, "Input reset wiring is not implemented yet");
    }
}
