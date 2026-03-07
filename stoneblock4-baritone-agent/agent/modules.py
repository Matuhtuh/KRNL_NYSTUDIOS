from __future__ import annotations

from typing import Any


def module_tasks(name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    p = params or {}
    module = name.strip().lower()

    if module == "starter_safe_tunnel":
        length = max(8, int(p.get("length", 32)))
        width = max(1, int(p.get("width", 2)))
        height = max(2, int(p.get("height", 2)))
        return [
            {
                "type": "build_tunnel",
                "width": width,
                "height": height,
                "length": length,
                "segment_length": min(24, length),
                "verify_item_ids": ["minecraft:cobblestone", "minecraft:cobbled_deepslate"],
            },
        ]

    if module == "stoneblock_hammer_brush_loop":
        # Uses gather+craft fallbacks; exact hammer/brush interaction remains modpack-specific.
        cycles = max(1, int(p.get("cycles", 2)))
        tasks: list[dict[str, Any]] = []
        for _ in range(cycles):
            tasks.extend(
                [
                    {
                        "type": "build_tunnel",
                        "width": 2,
                        "height": 2,
                        "length": 16,
                        "segment_length": 16,
                        "verify_item_ids": ["minecraft:cobblestone", "minecraft:cobbled_deepslate"],
                    },
                    {"type": "quest_item_acquire", "item": "minecraft:gravel", "count": 8, "optional": True},
                    {"type": "quest_item_acquire", "item": "minecraft:sand", "count": 8, "optional": True},
                    {"type": "quest_item_acquire", "item": "minecraft:dirt", "count": 8, "optional": True},
                ]
            )
        return tasks

    if module == "basic_machine_bootstrap":
        return [
            {"type": "quest_item_acquire", "item": "minecraft:crafting_table", "count": 1, "optional": True},
            {"type": "quest_item_acquire", "item": "minecraft:furnace", "count": 1, "optional": True},
            {"type": "quest_item_acquire", "item": "mekanism:metallurgic_infuser", "count": 1, "optional": True},
            {"type": "quest_item_acquire", "item": "oritech:machine_frame", "count": 1, "optional": True},
        ]

    if module == "basic_farm_bootstrap":
        return [
            {"type": "quest_item_acquire", "item": "minecraft:dirt", "count": 32, "optional": False},
            {"type": "quest_item_acquire", "item": "minecraft:wheat_seeds", "count": 8, "optional": True},
            {"type": "note", "text": "Farm bootstrap module executed. Expand with server-specific crop automation tasks."},
        ]

    if module == "idle_resource_grind":
        cycles = max(1, int(p.get("cycles", 1)))
        segment = max(8, min(20, int(p.get("segment_length", 12))))
        require_progress = bool(p.get("require_progress", True))
        include_fallback_probe = bool(p.get("include_fallback_probe", True))
        probe_segment = max(4, min(8, int(p.get("probe_segment_length", 6))))
        tasks: list[dict[str, Any]] = []
        for _ in range(cycles):
            tasks.append(
                {
                    "type": "build_tunnel",
                    "width": 2,
                    "height": 2,
                    "length": segment,
                    "segment_length": segment,
                    "verify_item_ids": ["minecraft:cobblestone", "minecraft:cobbled_deepslate"],
                    "require_progress": require_progress,
                }
            )
            if include_fallback_probe:
                tasks.append(
                    {
                        "type": "build_tunnel",
                        "width": 2,
                        "height": 2,
                        "length": probe_segment,
                        "segment_length": probe_segment,
                        "verify_item_ids": ["minecraft:cobblestone", "minecraft:cobbled_deepslate"],
                        "require_progress": False,
                    }
                )
            tasks.extend(
                [
                    {"type": "quest_item_acquire", "item": "minecraft:gravel", "count": 16, "optional": True},
                    {"type": "quest_item_acquire", "item": "minecraft:sand", "count": 16, "optional": True},
                    {"type": "quest_item_acquire", "item": "minecraft:dirt", "count": 16, "optional": True},
                    {"type": "quest_item_acquire", "item": "minecraft:cobblestone", "count": 256, "optional": True},
                ]
            )
        return tasks

    if module == "idle_recovery_housekeeping":
        cycles = max(1, int(p.get("cycles", 1)))
        tasks: list[dict[str, Any]] = []
        for _ in range(cycles):
            tasks.extend(
                [
                    {"type": "quest_item_acquire", "item": "minecraft:gravel", "count": 12, "optional": True},
                    {"type": "quest_item_acquire", "item": "minecraft:dirt", "count": 12, "optional": True},
                    {"type": "quest_item_acquire", "item": "minecraft:sand", "count": 12, "optional": True},
                    {"type": "quest_item_acquire", "item": "minecraft:cobblestone", "count": 128, "optional": True},
                ]
            )
        return tasks

    if module == "base_storage_housekeeping":
        cycles = max(1, int(p.get("cycles", 1)))
        radius = max(2, min(12, int(p.get("radius", 6))))
        max_stacks = max(1, min(36, int(p.get("max_stacks", 24))))
        tasks: list[dict[str, Any]] = []
        for _ in range(cycles):
            tasks.extend(
                [
                    {"type": "quest_item_acquire", "item": "minecraft:chest", "count": 1, "optional": True},
                    {"type": "place_storage_chest", "count": 1, "optional": True},
                    {
                        "type": "store_inventory_in_chest",
                        "radius": radius,
                        "max_stacks": max_stacks,
                        "include_hotbar": False,
                        "optional": True,
                    },
                ]
            )
        return tasks

    raise ValueError(f"Unknown module: {name}")
