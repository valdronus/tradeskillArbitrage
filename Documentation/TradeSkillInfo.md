# TradeSkillInfo (commit `acd0e2c`)

This document describes the structure of the `TradeSkillInfo` addon as of commit `acd0e2c` (`acd0e2cf8e199494a25aa0318160a992031f9fde`, dated Sun Sep 5 2010). That makes this snapshot a late-summer 2010, pre-Cataclysm version that is based on Wrath of the Lich King (WotLK) tradeskill data.

## Table of Contents

- [Overview](#overview)
- [Commit Context](#commit-context)
- [Repository Layout](#repository-layout)
  - [Root Files](#root-files)
  - [Localization Files](#localization-files)
  - [UI Addon (`TradeskillInfoUI/`)](#ui-addon-tradeskillinfoui)
- [Core Code Files](#core-code-files)
  - [`TradeskillInfo.lua`](#tradeskillinfolua)
  - [`TradeskillData.lua`](#tradeskilldatalua)
  - [`TradeskillIdData.lua`](#tradeskilliddatalua)
- [Data Storage and Recipe Location](#data-storage-and-recipe-location)
  - [How recipe/formula/skill data is stored](#how-recipeformula-skill-data-is-stored)
  - [Where to find profession recipe data](#where-to-find-profession-recipe-data)
- [Other Supporting Files](#other-supporting-files)

## Overview

`TradeSkillInfo` is a World of Warcraft addon that augments tradeskill tooltips and the tradeskill UI with recipe knowledge, required skill level, reagent sources, vendor prices, profit math, and availability information. Its purpose is to give players a complete picture of crafting recipes and how those recipes are obtained so they can decide what to craft, what to farm, and what to buy without leaving the game. This addon surfaces that data directly in the tradeskill UI and tooltips, helping players make informed crafting decisions at a glance. In this commit, the addon is tailored to WotLK-era data and does not include Cataclysm or pre-patch recipe changes.

## Commit Context

- Commit: `acd0e2c`
- Full SHA: `acd0e2cf8e199494a25aa0318160a992031f9fde`
- Author: `nowhererx7`
- Date: `Sun Sep 5 02:03:02 2010 +0000`
- Message: `TradeskillInfo: - redo of r364; forgot that Wordpad destroys lua files`

This commit sits squarely in the WotLK timeline; Cataclysm was still months away, so the recipe database and tradeskill mappings here represent the game state before Cataclysm updates.

## Repository Layout

The addon files are located under `Documentation/References/TradeSkillInfo/` in this workspace. The optional UI extension is under `Documentation/References/TradeSkillInfo/TradeskillInfoUI/`.

### Root Files

- `TradeskillInfo.toc`
  - Addon manifest for `TradeSkillInfo`.
  - Lists embedded libraries, required files, localization files, and the main Lua files.
- `Bindings.xml`
  - Defines key bindings for opening the TradeskillInfo window.
- `TradeskillInfo.lua`
  - Main addon logic and runtime glue.
- `TradeskillData.lua`
  - Static lookup tables for spells, sources, zones, factions, and vendors.
- `TradeskillIdData.lua`
  - Main recipe/recipe ID database, including combines, components, special cases, and recipe sources.
- `embeds.xml`
  - Lists embedded libraries that the addon bundles.
- `locale-*.lua`
  - Language-localized strings for the addon.

### Localization Files

- `locale-enUS.lua`
- `locale-ruRU.lua`
- `locale-deDE.lua`
- `locale-esES.lua`
- `locale-koKR.lua`
- `locale-zhCN.lua`
- `locale-zhTW.lua`
- `locale-frFR.lua`

Each of these files provides localized text used by the addon. They do not contain trade data, but they do supply labels, source names, and UI strings.

### UI Addon (`TradeskillInfoUI/`)

- `TradeskillInfoUI.toc`
  - Addon manifest for the optional UI extension.
- `TradeskillInfoUI.lua`
  - UI code for the standalone TradeskillInfo browser window and search UI.
- `TradeskillInfoUI.xml`
  - XML layout for the custom UI panel.
- `TradeskillInfoOptions.lua`
  - Option handling and AceConfig support for UI settings.
- `locale-*.lua`
  - UI-specific localization strings.
- `images/`
  - Any UI images used by the addon.

The UI addon is optional, but it provides search and browse capabilities for the data that the core addon stores.

## Core Code Files

### `TradeskillInfo.lua`

This is the primary runtime file for the addon.

- Registers the addon with AceAddon-3.0 and hooks into WoW events.
- Sets up saved variable defaults for `TradeskillInfoDB`.
- Handles skill updates, recipe scanning, tooltip hooks, and UI hooks.
- Implements helper functions such as `GetCombine()`, `GetCombineSkill()`, `GetCombineComponents()`, `GetCombineRecipe()`, and `GetCombineDifficulty()`.
- Loads the optional `TradeskillInfoUI` addon when needed.
- Defines the logic for detecting recipe IDs from item links and spell links.
- Builds the "where used" index that connects components back to recipes.

Key hooks and functions in `TradeskillInfo.lua`:

- `OnInitialize()`
  - Initializes the database and registers chat commands.
- `OnEnable()`
  - Initializes player data, hooks trade skill and auction UIs, and registers events.
- `OnSkillUpdate()` / `UpdateKnownRecipes()`
  - Tracks known recipes and character skill levels.
- `GetExtraItemDetailText()`
  - Generates tooltip text for a recipe, using recipe/price data.
- `GetCombine()` / `CombineExists()` / `GetCombineSkill()`
  - Parse the recipe database entries in `TradeskillIdData.lua`.
- `Item_OnClick()`
  - Opens the search UI or prints where-used information on modifier-click.

### `TradeskillData.lua`

This file holds supporting static data and lookup tables that are not the main recipe database.

- `TradeskillInfo.vars.tradeskillspells`
  - Maps profession codes like `A`, `B`, `D`, `E`, `J`, `L`, `T`, `W`, `X`, `Y`, `I` to their spell IDs.
- `TradeskillInfo.vars.specializationspells`
  - Maps specialization codes like armor/weapon smithing, leatherworking specializations, engineering branches, and tailoring branches to spell IDs.
- `TradeskillInfo.vars.sources`
  - Maps source codes used in `recipes` and `vendors` to localized source text like `Vendor`, `Dropped`, `Quest`, `Trainer`, `Alchemy`, `Blacksmithing`, `Inscription`, etc.
- `TradeskillInfo.vars.zones`
  - Maps numeric zone IDs to localized zone names.
- `TradeskillInfo.vars.factions`
  - Maps faction IDs to localized faction names, including WotLK factions such as Kirin Tor, Sunreavers, Argent Crusade, and others.
- `TradeskillInfo.vars.vendors`
  - Vendor lookup data with strings like `name|zone|faction|location|comment`.

This file is primarily about metadata and world data used when interpreting recipe sources, vendor locations, and skill categories.

### `TradeskillIdData.lua`

This is the main data file for recipe definitions and item mapping.

- Defines `TradeskillInfo.vars.difficultyLevel` and color values for recipe difficulty.
- Defines `TradeskillInfo.vars.specialcases`
  - Special-case item IDs for recipes that map to the same item result but require different spell IDs or recipe handling.
- Defines `TradeskillInfo.vars.combines`
  - The master recipe table.
  - Each entry is indexed by crafted `itemid` or a negative enchant ID for non-item combines.
  - Values are strings encoding `spell|skill|components|recipe|yield|itemid`.
  - This table is the core source of WotLK crafting formulas and recipe requirements.
- Defines `TradeskillInfo.vars.components`
  - A lookup table for component sources.
  - Uses a metatable to infer component source from a combine entry if not directly defined.
- Defines `TradeskillInfo.vars.recipes`
  - Maps `recipeid` to `result|source|price|factionrank`.
  - Stores how recipes are obtained: vendor, drop, quest, faction rank, etc.

This file is the single best place to look for actual recipe formulas, component lists, and source metadata.

## Data Storage and Recipe Location

### How recipe/formula/skill data is stored

`TradeSkillInfo` stores its WotLK recipe data across two main data files:

- `TradeskillData.lua`
  - Metadata and lookup tables.
  - Profession IDs, source labels, zone names, faction names, and vendor definitions.
- `TradeskillIdData.lua`
  - Recipe definitions (`combines`).
  - Component source mappings (`components`).
  - Recipe acquisition data (`recipes`).
  - Special case remapping for ambiguous item IDs (`specialcases`).

### Where to find profession recipe data

For every profession, the recipe database is primarily in `TradeskillIdData.lua`.

- `TradeskillInfo.vars.combines`
  - Each key is a crafted item ID or a negative enchant ID.
  - Each string value encodes the crafting spell, profession code, required skill level, reagent IDs/amounts, recipe ID, and optional yield or alternate output item.
  - Example:
    - `[2454] = "2329|A1/55/75/95|2449 765 3371"` means item ID `2454` is crafted by spell `2329`, uses Alchemy (`A`), has skill tiers `1/55/75/95`, and requires reagents `2449`, `765`, and `3371`.
- `TradeskillInfo.vars.getCombineComponents(id)`
  - Runtime helper in `TradeskillInfo.lua` parses the component string from `combines` and returns reagent details.

For all formulas, recipe IDs, and skills:

- `TradeskillInfo.vars.recipes`
  - Contains recipe source metadata keyed by recipe ID.
  - Each entry is a string like `result|source|price|factionrank`.
  - Example: `[728] = "733|V92Qa|200"` means recipe `728` creates item `733`, is sold by vendor `92` at alliance reputation rank, and costs `200` copper.
- `TradeskillInfo.vars.components`
  - Maps item IDs to source codes indicating how reagents are obtained.
  - If a component is itself craftable and appears in `combines`, the table can infer source from the profession.
- `TradeskillInfo.vars.tradeskillspells`
  - Maps profession codes to the base spell IDs needed to identify a profession.
  - This allows the addon to correlate a recipe entry with the correct tradeskill.
- `TradeskillInfo.vars.specialcases`
  - Handles recipes and items that require special matching across multiple spell IDs or ambiguous item IDs.

### Where data for all formulas / skills / recipes are stored

- `TradeskillInfo.vars.combines` in `TradeskillIdData.lua`
  - Central formula table. This is the place to inspect or extend WotLK recipe definitions.
- `TradeskillInfo.vars.recipes` in `TradeskillIdData.lua`
  - Recipe acquisition metadata for vendors, drops, quests, and faction sources.
- `TradeskillInfo.vars.components` in `TradeskillIdData.lua`
  - Component source mapping and inferred skill mapping for reagents.
- `TradeskillInfo.vars.tradeskillspells` in `TradeskillData.lua`
  - Profession-to-spell mapping used to identify which profession a recipe belongs to.
- `TradeskillInfo.vars.specializationspells` in `TradeskillData.lua`
  - Specialization spell IDs used for sub-professions and apprentice/master branches.

## Other Supporting Files

- `Bindings.xml`
  - Defines keyboard bindings for quick addon access.
- `embeds.xml`
  - Lists embedded library dependencies.
- `TradeskillInfoUI/TradeskillInfoUI.lua`
  - Implements the optional UI browser and search panel.
  - Uses the core data from `TradeskillInfo.lua`, `TradeskillData.lua`, and `TradeskillIdData.lua`.
- `TradeskillInfoUI/TradeskillInfoUI.xml`
  - UI layout for the TradeskillInfo window.
- All `locale-*.lua` files
  - Provide translations for source names, UI labels, and add-on text.

## Notes for future use

- When searching for WotLK tradeskill data, start with `TradeskillIdData.lua` for recipe formulas and `TradeskillData.lua` for source/type mappings.
- `TradeskillInfo.lua` is the runtime layer that parses the string-encoded recipe data and presents it to tooltips and UI elements.
- This commit is intentionally pre-Cataclysm, so it is appropriate for WotLK-era recipe extraction or analysis.

## Extraction script plan

The extractor should live in the repository root and focus on parsing `Documentation/References/TradeSkillInfo/TradeskillIdData.lua`.

### Goals
- Emit a JSON file of basic craft recipes.
- Capture output item ID, spell ID, profession, skill levels, yield, recipe ID, and component list.
- Keep the extraction logic simple for standard recipes that consume reagents and produce an item.
- Follow existing root-script conventions for CLI, file handling, and Lua parsing.

### Input data sources
- `TradeskillInfo.vars.combines` in `TradeskillIdData.lua`
  - Core formula table.
  - Each entry is a Lua table key/value pair.
  - Values are strings in the form `spell|skill|components|recipe|yield|itemid`.
- `TradeskillInfo.vars.recipes` in `TradeskillIdData.lua` (optional enrichment)
  - Maps recipe IDs to acquisition metadata.
- `TradeskillInfo.vars.components` in `TradeskillIdData.lua` (optional enrichment)
  - Maps reagent item IDs to source categories.

### Parsing approach
- Use `slpp` to parse the Lua file into Python dictionaries for `combines`, `recipes`, and `components`.
- Strip leading `return` and any top-level assignment syntax before decoding.
- If `slpp` cannot parse the full file, extract the relevant table block and parse only that text.
- Treat the `combines` table as the authoritative source of recipe formulas.

### Major function flow
1. `parse_lua_file(path, logger)`
   - Read the Lua source file.
   - Trim whitespace, remove a leading `return`, and decode with `slpp`.
   - Return the parsed Lua object.

2. `load_lua_tables(path, logger)`
   - Load the Lua file with `parse_lua_file`.
   - Extract `TradeskillInfo.vars.combines`, `TradeskillInfo.vars.recipes`, and
     optionally `TradeskillInfo.vars.components`.
   - Return parsed dictionaries.

3. `parse_combine_entry(key, value)`
   - Accept a combine key and its string value.
   - Split the value by `|` into fields:
     - `spell_id`, `skill_spec_level`, `component_string`, `recipe_id`,
       `yield`, `output_override`
   - Parse `skill_spec_level` into `profession`, `specialization`, and numeric
     tiers.
   - Parse `component_string` by whitespace and `:` into component IDs and
     quantities.
   - Determine the recipe output item ID:
     - use `output_override` if present, otherwise use the table key.
   - Normalize missing fields: default `yield = 1`, `recipe_id = null`,
     `specialization = ""`.
   - Return a normalized recipe dictionary.

4. `normalize_skill_tiers(skill_spec_level)`
   - Split the skill string like `T1/25/37/50`.
   - Map the first number to the base skill level and the remaining values to
     difficulty thresholds.
   - Preserve both raw tier list and parsed numeric values.

5. `build_recipe_json(combines)`
   - Iterate over all parsed combine entries.
   - Skip entries with negative keys if the goal is regular item recipes; or
     include them separately if enchantments are needed.
   - Convert each entry with `parse_combine_entry`.
   - Collect recipe objects in a list.

6. `enrich_recipes(recipes, components, specialcases)` (optional)
   - Attach recipe acquisition details from `recipes` to recipe objects.
   - Attach reagent source type from `components` to each component entry.
   - Use `specialcases` only if needed for alias resolution.

7. `write_json(output_path, data)`
   - Serialize the recipe list to JSON.
   - Use stable formatting, `indent=2`, and UTF-8 encoding.

### Output shape
A basic JSON object for each recipe should include:
- `output_item_id`
- `spell_id`
- `profession`
- `specialization`
- `skill_level`
- `difficulty_tiers`
- `yield`
- `recipe_id`
- `components`
  - `item_id`
  - `quantity`
  - optional `source` if enriched

### Important edge cases
- `specialcases` entries are alias mappings and do not represent direct formula
  structure.
- Negative `combines` keys represent enchantments and spell-based recipes.
- Some combine entries override the output item ID with a sixth field.
- `recipe` field may be absent; the formula still remains valid.

### Root-script conventions to adopt
- Use `argparse` and `pathlib.Path`.
- Provide a `main()` entry point and exit cleanly with `sys.exit()`.
- Use `logging` for diagnostics and parse failures.
- Follow existing JSON output formatting:
  `json.dumps(..., indent=2, ensure_ascii=False)`.

### Formatting and linting
- Aim to wrap lines at 80 characters where reasonable.
- Allow up to 120 characters for readability, but keep the code style
  disciplined and consistent.
- Suggested checks:
  - `python -m py_compile extract_tradeskillinfo_recipes.py`
  - `python -m pip install ruff` then `ruff check extract_tradeskillinfo_recipes.py`
  - `python -m pip install black` then `black --line-length 120 extract_tradeskillinfo_recipes.py`
- Keep the script simple and readable rather than enforcing a strict
  one-size-fits-all width.

### Placement
- Store the script at the repository root.
- Name it clearly, such as `extract_tradeskillinfo_recipes.py`.
- Optionally output `tradeskillinfo_recipes.json` next to the script.

### Summary
The extraction script should be a linear pipeline: parse Lua tables with
`slpp`, normalize each `combines` entry, optionally enrich with recipe and
component metadata, then write a simple JSON representation for item crafting
recipes.
