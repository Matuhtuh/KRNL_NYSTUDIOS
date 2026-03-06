package com.nystudios.stoneblock4bridge.control;

import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import java.util.Map;

/**
 * Deterministic low-level player control contract for movement and interaction.
 */
public interface PlayerController {
    ActionResultDto performMovementInput(String requestId, Map<String, String> parameters);

    ActionResultDto interactWithBlock(String requestId, Map<String, String> parameters);

    ActionResultDto stopAllInputs(String requestId);
}
