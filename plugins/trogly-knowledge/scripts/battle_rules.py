"""RAID's battle formulas, read out of GameAssembly.dll (build of 2026-10-19; addresses below are from that build).

Every function names the game method it copies. The constants the game keeps in its static data (affinity and hit
coefficients) come from the knowledge file (`battle`); `GAMEPLAY_DEFAULTS` only covers knowledge files exported before
the Agent read them, and answers say so. Stdlib only.
"""

from __future__ import annotations

import math
import random
from typing import Any

# StaticGameplayData fields the Agent exports into knowledge.json → battle. Defaults are used, and reported, only when
# the knowledge file is older than the export of these fields.
GAMEPLAY_FIELDS = (
    "element_disadvantage_coef", "crushing_hit_coef", "glancing_hit_coef", "critical_heal_coef",
    "critical_hit_chance", "crushing_hit_chance", "glancing_hit_chance", "critical_hit_chance_advantage",
    "max_applied_buff_effects", "max_applied_debuff_effects", "counterattack_modifier", "shield_cap_value",
)
GAMEPLAY_DEFAULTS = {
    "element_disadvantage_coef": -0.2,
    "crushing_hit_coef": 0.3,
    "glancing_hit_coef": -0.3,
    "critical_heal_coef": 0.0,
    "critical_hit_chance": 0.0,
    "crushing_hit_chance": 0.4,
    "glancing_hit_chance": 0.35,
    "critical_hit_chance_advantage": 0.15,
    "max_applied_buff_effects": 10,
    "max_applied_debuff_effects": 10,
    "counterattack_modifier": 0.0,
    "shield_cap_value": 0.0,
}

# Affinity wheel (Element: 1 Magic, 2 Force, 3 Spirit, 4 Void). Void is neutral both ways.
BEATS = {"Magic": "Spirit", "Spirit": "Force", "Force": "Magic"}

NORMAL, CRUSHING, CRITICAL, GLANCING = "normal", "crushing", "critical", "glancing"


def gameplay_constants(knowledge_battle: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    """The game's battle constants, plus the names of any that fell back to defaults."""
    constants, missing = {}, []
    for name in GAMEPLAY_FIELDS:
        if knowledge_battle.get(name) is not None:
            constants[name] = float(knowledge_battle[name])
        else:
            constants[name] = GAMEPLAY_DEFAULTS[name]
            missing.append(name)
    return constants, missing


def element_relation(attacker: str | None, defender: str | None) -> int:
    """ElementRelation: 1 advantage, 0 neutral, -1 disadvantage."""
    if not attacker or not defender:
        return 0
    if BEATS.get(attacker) == defender:
        return 1
    if BEATS.get(defender) == attacker:
        return -1
    return 0


def resist_chance(accuracy: float, resistance: float) -> float:
    """BattleProcessor.IsResisted (0x182DA0130): chance a debuff is resisted, from RES minus ACC.

    Below a 30-point gap the chance climbs from 3% towards 30%; above it, from 30% towards 97%.
    """
    gap = resistance - accuracy
    if gap < 30:
        return 0.03 + 0.27 * math.exp(6 * (gap / 100 - 0.3))
    return 0.3 + 0.67 * (1 - math.exp(3 * (0.3 - gap / 100)))


def is_resisted(accuracy: float, resistance: float, rng: random.Random) -> bool:
    """The game rolls one random number and resists when it is at or under the chance."""
    return rng.random() <= resist_chance(accuracy, resistance)


def defence_factor(defence: float, flat_ignore: float = 0.0, defence_modifier: float = 0.0) -> float:
    """DamageCalculator.DamageReductionByDefence (0x182D868A0): the share of damage left after DEF.

    Effective DEF is (DEF - flat ignore) x (1 + modifier); the modifier is negative for ignore-DEF effects. At most 85% of
    the damage is absorbed: 1 - 0.85 x (1 - e^(-DEF / 1500)).
    """
    effective = (defence - flat_ignore) * (1 + defence_modifier)
    return 1 - 0.85 * (1 - math.exp(-2 * effective / 3000))


def hit_type(relation: int, crit_chance: float, rng: random.Random, constants: dict[str, float],
             can_glance: bool = True, can_crush: bool = True) -> str:
    """DamageCalculator.CalculateHitType (0x182D86020): one roll decides the hit.

    Glance is checked first (only on a disadvantage), then crit (an advantage adds critical_hit_chance_advantage), then
    crush (only on an advantage). A chance of 100% or more always hits; otherwise the roll must be under it.
    """
    roll = rng.random()

    def hits(chance: float) -> bool:
        return chance > 0 and (chance >= 1 or roll < chance)

    if relation == -1 and can_glance and hits(constants["glancing_hit_chance"]):
        return GLANCING
    crit = crit_chance + (constants["critical_hit_chance_advantage"] if relation == 1 else 0)
    if hits(crit):
        return CRITICAL
    if relation == 1 and can_crush and hits(constants["crushing_hit_chance"]):
        return CRUSHING
    return NORMAL


def damage_multipliers(relation: int, hit: str, critical_damage: float, book_bonus: float, extra: float,
                       stat_modifier: float, constants: dict[str, float]) -> float:
    """DamageCalculator.CalculateDamage (0x182D85A50): the skill's value is multiplied by
    (1 + book damage bonus) x (1 + affinity) x (1 + hit bonus) x (1 + extra) x (1 + stat modifier).

    Affinity only counts against: element_disadvantage_coef on a disadvantage. The hit bonus is the attacker's C.DMG on a
    crit, crushing_hit_coef on a crushing hit, glancing_hit_coef on a glancing hit (HitTypeBonus, 0x182D877D0).
    """
    affinity = constants["element_disadvantage_coef"] if relation == -1 else 0.0
    bonus = {CRITICAL: critical_damage, CRUSHING: constants["crushing_hit_coef"], GLANCING: constants["glancing_hit_coef"]}.get(hit, 0.0)
    return (1 + book_bonus) * (1 + affinity) * (1 + bonus) * (1 + extra) * (1 + stat_modifier)


def damage_roll(rng: random.Random) -> float:
    """DamageCalculator.Calculate (0x182D862A0): damage that isn't fixed is multiplied by Next(90, 110) / 100."""
    return rng.randrange(90, 110) / 100


def stat_damage_modifier(attacker: dict[str, float], target: dict[str, float], area: str, attacker_is_boss: bool,
                         target_is_boss: bool) -> float:
    """DamageCalculator.DamageModifierFromStats (0x182D86620): the conditional damage stats (blessings, relics, ...).

    Against computer-run enemies: dealt PvE + taken PvE, plus the dungeon pair in dungeons, plus taken-from-boss when a
    boss hits and dealt-to-boss when a boss is hit. Against players: dealt PvP + taken PvP.
    """
    if area in ("Arena", "Arena3X3", "LiveArena"):
        return attacker.get("dealt_damage_pvp", 0) + target.get("taken_damage_pvp", 0)
    total = attacker.get("dealt_damage_pve", 0) + target.get("taken_damage_pve", 0)
    if area == "Dungeon":
        total += attacker.get("dealt_damage_dungeons", 0) + target.get("taken_damage_dungeons", 0)
    if attacker_is_boss:
        total += target.get("taken_damage_boss", 0)
    if target_is_boss:
        total += attacker.get("dealt_damage_boss", 0)
    return total
