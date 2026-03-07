from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Recipe:
    output_item: str
    output_count: int
    ingredients: dict[str, int]
    recipe_type: str
    source_file: str


@dataclass
class CraftPlan:
    target_item: str
    target_count: int
    steps: list[tuple[str, int]]
    base_requirements: dict[str, int]


def _extract_output(payload: dict[str, Any]) -> tuple[str, int]:
    def _output_stack(node: Any) -> tuple[str, int]:
        if isinstance(node, str):
            token = node.strip()
            return token, 1 if token else 0
        if isinstance(node, dict):
            nested = node.get("stack")
            if nested is not None:
                return _output_stack(nested)
            item_id = str(
                node.get("id")
                or node.get("item")
                or node.get("item_id")
                or node.get("result")
                or ""
            ).strip()
            if not item_id:
                return "", 0
            raw_count = node.get("count", node.get("amount", node.get("resultCount", 1)))
            try:
                count = int(raw_count)
            except Exception:
                count = 1
            return item_id, max(1, count)
        if isinstance(node, list):
            for entry in node:
                item_id, count = _output_stack(entry)
                if item_id and count > 0:
                    return item_id, count
        return "", 0

    for key in ("result", "output", "main_output", "item_output", "results", "outputs", "secondary_output"):
        if key not in payload:
            continue
        item_id, count = _output_stack(payload.get(key))
        if item_id and count > 0:
            return item_id, count
    nested_recipe = payload.get("recipe")
    if isinstance(nested_recipe, dict):
        return _extract_output(nested_recipe)
    return "", 0


def _ingredient_item_and_count(ingredient: Any) -> tuple[str, int]:
    if isinstance(ingredient, str):
        token = ingredient.strip()
        if token and not token.startswith("#"):
            return token, 1
        return "", 0
    if isinstance(ingredient, dict):
        item_id = str(
            ingredient.get("item")
            or ingredient.get("id")
            or ingredient.get("item_id")
            or ""
        ).strip()
        if item_id:
            raw_count = ingredient.get("count", ingredient.get("amount", 1))
            try:
                count = int(raw_count)
            except Exception:
                count = 1
            return item_id, max(1, count)

        for nested_key in ("ingredient", "input", "item_input", "output", "stack", "basePredicate"):
            if nested_key in ingredient:
                return _ingredient_item_and_count(ingredient.get(nested_key))

        # Tag-only ingredients are intentionally ignored for now.
        return "", 0
    if isinstance(ingredient, list):
        for variant in ingredient:
            item_id, count = _ingredient_item_and_count(variant)
            if item_id:
                return item_id, max(1, count)
    return "", 0


def _extract_ingredients(payload: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    recipe_type = str(payload.get("type", ""))

    pattern_node = payload.get("pattern")
    if isinstance(pattern_node, dict):
        key_map = pattern_node.get("key", {})
        pattern_rows = pattern_node.get("pattern", [])
    else:
        key_map = payload.get("key", {})
        pattern_rows = payload.get("pattern", [])

    if key_map and pattern_rows:
        symbol_counts: dict[str, int] = {}
        for row in pattern_rows:
            for ch in str(row):
                if ch == " ":
                    continue
                symbol_counts[ch] = symbol_counts.get(ch, 0) + 1
        for symbol, symbol_count in symbol_counts.items():
            ingredient = key_map.get(symbol)
            item_id, ingredient_count = _ingredient_item_and_count(ingredient)
            if not item_id:
                continue
            out[item_id] = out.get(item_id, 0) + symbol_count * max(1, ingredient_count)
        return out

    for list_key in ("ingredients", "inputs", "item_inputs", "input_items", "pedestalItems", "cast"):
        if not isinstance(payload.get(list_key), list):
            continue
        for ingredient in payload.get(list_key, []):
            node = ingredient
            multiplier = 1
            if isinstance(ingredient, dict):
                if "ingredient" in ingredient:
                    node = ingredient.get("ingredient")
                raw_multi = ingredient.get("count", ingredient.get("amount", 1))
                try:
                    multiplier = max(1, int(raw_multi))
                except Exception:
                    multiplier = 1
            item_id, ingredient_count = _ingredient_item_and_count(node)
            if not item_id:
                continue
            out[item_id] = out.get(item_id, 0) + max(1, ingredient_count) * max(1, multiplier)
        if out:
            return out

    for single_key in (
        "ingredient",
        "input",
        "item_input",
        "main_input",
        "extra_input",
        "reagent",
        "catalyst",
        "input0",
        "input1",
        "egg",
    ):
        if single_key not in payload:
            continue
        item_id, ingredient_count = _ingredient_item_and_count(payload.get(single_key))
        if item_id:
            out[item_id] = out.get(item_id, 0) + max(1, ingredient_count)
    if out:
        return out

    # Some modded recipes use custom keys such as "ingredientsA/B"; keep parser conservative.
    if recipe_type.endswith("smithing_transform") or recipe_type.endswith("smithing_trim"):
        for key in ("base", "addition", "template"):
            item_id, ingredient_count = _ingredient_item_and_count(payload.get(key))
            if item_id:
                out[item_id] = out.get(item_id, 0) + max(1, ingredient_count)
        if out:
            return out

    nested_recipe = payload.get("recipe")
    if isinstance(nested_recipe, dict):
        nested_out = _extract_ingredients(nested_recipe)
        if nested_out:
            return nested_out
    return out


class RecipeIndex:
    def __init__(self, recipes: list[Recipe]) -> None:
        self.recipes = recipes
        by_output: dict[str, list[Recipe]] = {}
        for recipe in recipes:
            by_output.setdefault(recipe.output_item, []).append(recipe)
        self.by_output = by_output

    @staticmethod
    def _recipe_files(instance_root: Path) -> list[Path]:
        files: list[Path] = []
        for base in (
            instance_root / "datapacks",
            instance_root / "kubejs",
        ):
            if not base.exists():
                continue
            for p in base.rglob("*.json"):
                low = str(p).lower().replace("\\", "/")
                if "/advancement/recipes/" in low:
                    continue
                if "/recipe/" in low or "/recipes/" in low:
                    files.append(p)
        return files

    @classmethod
    def from_instance(cls, instance_path: str | Path) -> "RecipeIndex":
        root = Path(instance_path).expanduser().resolve()
        recipes: list[Recipe] = []
        for recipe_file in cls._recipe_files(root):
            try:
                payload = json.loads(recipe_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if not payload.get("type"):
                continue
            output_item, output_count = _extract_output(payload)
            if not output_item or output_count <= 0:
                continue
            ingredients = _extract_ingredients(payload)
            if not ingredients:
                continue
            recipes.append(
                Recipe(
                    output_item=output_item,
                    output_count=max(1, output_count),
                    ingredients=ingredients,
                    recipe_type=str(payload.get("type", "")),
                    source_file=str(recipe_file),
                )
            )
        return cls(recipes)

    def _select_recipe(self, item_id: str) -> Recipe | None:
        choices = self.by_output.get(item_id, [])
        if not choices:
            return None
        # Prefer the simplest concrete recipe to keep plans stable.
        return sorted(choices, key=lambda r: (len(r.ingredients), sum(r.ingredients.values())))[0]

    def plan(self, target_item: str, target_count: int, max_depth: int = 8) -> CraftPlan:
        target = str(target_item).strip()
        count = max(1, int(target_count))
        craft_steps: list[tuple[str, int]] = []
        base_requirements: dict[str, int] = {}
        in_stack: set[str] = set()

        def expand(item: str, needed: int, depth: int) -> None:
            if depth > max_depth:
                base_requirements[item] = base_requirements.get(item, 0) + needed
                return
            if item in in_stack:
                base_requirements[item] = base_requirements.get(item, 0) + needed
                return

            recipe = self._select_recipe(item)
            if recipe is None:
                base_requirements[item] = base_requirements.get(item, 0) + needed
                return

            in_stack.add(item)
            multiplier = max(1, math.ceil(needed / max(1, recipe.output_count)))
            for ingredient_item, ingredient_count in recipe.ingredients.items():
                expand(ingredient_item, ingredient_count * multiplier, depth + 1)
            in_stack.remove(item)

            craft_steps.append((item, recipe.output_count * multiplier))

        expand(target, count, 0)

        merged_steps: dict[str, int] = {}
        order: list[str] = []
        for item_id, qty in craft_steps:
            if item_id not in merged_steps:
                order.append(item_id)
                merged_steps[item_id] = 0
            merged_steps[item_id] += qty

        ordered_steps = [(item_id, merged_steps[item_id]) for item_id in order]
        return CraftPlan(
            target_item=target,
            target_count=count,
            steps=ordered_steps,
            base_requirements=base_requirements,
        )
