package com.nystudios.stoneblock4bridge.bridge;

import com.nystudios.stoneblock4bridge.dto.ActionRequestDto;
import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import com.nystudios.stoneblock4bridge.dto.BridgeStateSnapshotDto;

/**
 * Client contract used by the external AI process. Added here as shared protocol documentation.
 */
public interface BridgeClient {
    BridgeStateSnapshotDto requestState();

    ActionResultDto sendAction(ActionRequestDto request);
}
