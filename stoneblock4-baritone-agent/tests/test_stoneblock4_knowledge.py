from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.stoneblock4_knowledge import build_stoneblock_knowledge


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class StoneBlockKnowledgeBuildTest(unittest.TestCase):
    def test_builds_local_routes_and_world_engine_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(
                root / "config/ftbquests/quests/chapters/getting_started.snbt",
                """
                {
                  id: "1111111111111111",
                  quests: [{
                    id: "2222222222222222",
                    tasks: [{ id: "3333333333333333", type: "item", item: { count: 1, id: "ftbstuff:stone_hammer" } }],
                    rewards: [{ id: "4444444444444444", type: "item", item: { count: 1, id: "minecraft:gravel" } }]
                  }]
                }
                """,
            )
            _write(
                root / "config/ftbquests/quests/lang/en_us.snbt",
                """
                chapter.1111111111111111.title: "Getting Started"
                quest.2222222222222222.title: "Hammer Time"
                """,
            )
            _write(
                root / "kubejs/server_scripts/recipes/mods/ftb/hammer.js",
                """
                const hammerRecipes = [
                  ["#c:cobblestones", "minecraft:gravel", 1],
                  ["#c:gravels", "minecraft:dirt", 1]
                ]
                """,
            )
            _write(
                root / "kubejs/server_scripts/recipes/mods/ftb/crook.js",
                """
                const crookRecipes = [
                  {
                    input: "minecraft:sand",
                    max: 4,
                    outputs: [["minecraft:sugar_cane", 1, 0.5]]
                  }
                ]
                """,
            )
            _write(
                root / "kubejs/server_scripts/recipes/mods/ftb/wooden_basin.js",
                """
                ServerEvents.recipes((event) => {
                  event.custom({
                    type: "ftbstuff:wooden_basin",
                    fluid: { amount: 250, id: "minecraft:water" },
                    input: "#minecraft:leaves"
                  })
                })
                """,
            )
            _write(
                root / "kubejs/server_scripts/recipes/mods/ftb/custom_machinery/world_engine_stage_checker.js",
                """
                const WE_STAGES = {
                  source_upgrade: "echo_magician_stage1_task2_check"
                }
                """,
            )
            _write(
                root / "kubejs/server_scripts/recipes/mods/ftb/custom_machinery/_machinery_structures.js",
                """
                const FTBStructures$WorldEngine = {
                  source_upgrade: {
                    keys: { a: "minecraft:diamond_block" },
                    pattern: [["a", "m"]]
                  }
                }
                """,
            )
            _write(
                root / "kubejs/server_scripts/event_handlers/player/quest/worldengine_quests.js",
                "function placeStructure() {}",
            )
            _write(
                root / "kubejs/server_scripts/recipes/mods/ftb/custom_machinery/world_engine.js",
                """
                ServerEvents.recipes(function (event) {
                  addWorldEngineRecipes(event, [
                    {
                      id: "ftb:world_engine/machine_frame",
                      machineId: "ftb:world_engine",
                      structures: ["source_upgrade"],
                      duration: 200,
                      itemInputs: [{ item: "minecraft:diamond", count: 1 }],
                      fluidInputs: [{ fluid: "minecraft:water", amount: 500, tank: "fluid_input_1" }],
                      itemOutputs: [{ item: "ftb:world_engine_machine_block", count: 16 }]
                    }
                  ])
                })
                const WORLDENGINE_AUTOBUILD_QUESTS = {
                  source_upgrade: "2222222222222222"
                }
                """,
            )

            out = root / "runtime/dashboard/stoneblock_knowledge.json"
            pack_model = root / "runtime/pack_model.json"
            payload = build_stoneblock_knowledge(root, pack_model_path=pack_model, out_path=out)

            route_ids = {route["routeId"] for route in payload["routes"]}
            self.assertIn("hammer__minecraft_cobblestone__minecraft_gravel", route_ids)
            self.assertIn("crook__minecraft_sand__minecraft_sugar_cane", route_ids)
            self.assertIn("world_engine__ftb_world_engine_machine_frame", route_ids)
            self.assertEqual(
                "echo_magician_stage1_task2_check",
                payload["worldEngine"]["upgradeGates"]["source_upgrade"]["stageFlag"],
            )
            machine_route = next(route for route in payload["routes"] if route["routeId"] == "world_engine__ftb_world_engine_machine_frame")
            self.assertEqual("fluid_input_1", machine_route["fluidInputs"][0]["tank"])
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
