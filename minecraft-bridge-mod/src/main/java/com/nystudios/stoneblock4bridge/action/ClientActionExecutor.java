package com.nystudios.stoneblock4bridge.action;

import com.nystudios.stoneblock4bridge.dto.ActionRequestDto;
import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import java.util.Set;

/**
 * Minimal action executor shell.
 *
 * <p>Only validates a narrow command set and returns explicit unsupported responses.
 * Real control wiring (movement/input/block interaction) should be added incrementally.</p>
 */
public final class ClientActionExecutor implements ActionExecutor {
    private static final Set<String> SUPPORTED_ACTION_TYPES = Set.of(
            "move_input",
            "look_delta",
            "jump",
            "sneak",
            "interact_block",
            "attack",
            "use_item",
            "stop_all_inputs"
    );

    @Override
    public ActionResultDto performAction(ActionRequestDto request) {
        if (!SUPPORTED_ACTION_TYPES.contains(request.actionType())) {
            return new ActionResultDto(request.requestId(), false, false, "Unsupported action type");
        }

        return new ActionResultDto(
                request.requestId(),
                false,
                false,
                "Action accepted by schema but not yet wired to Minecraft input controls"
        );
    }
}
