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

# DamageCalculator.DamageReductionByStatusEffects (0x182D86DA0) subtracts these from 1: a frozen target takes 25% less;
# Invisible1 / Invisible2 take 7.5% / 15% less from AoE damage. Code constants, not static data.
FREEZE_REDUCTION = 0.25
INVISIBLE_AOE_REDUCTION = {"Invisible1": 0.075, "Invisible2": 0.15}
# Effect kinds the game runs through StatusChangeDamageMultiplierProcessor (EffectProcessorContainer, 0x182DA4880):
# the status's own formula multiplies the hit's base value.
STATUS_MULTIPLIER_KINDS = {"IncreaseDamageTaken", "ReduceDamageTaken", "StatusChangeDamageMultiplier", "Rage", "Inspiration"}


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


def damage_multipliers(relation: int, hit: str, critical_damage: float, book_bonus: float, counterattack: float,
                       stat_modifier: float, constants: dict[str, float]) -> float:
    """DamageCalculator.CalculateDamage (0x182D85A50): the skill's value is multiplied by
    (1 + book damage bonus) x (1 + affinity) x (1 + hit bonus) x (1 + counterattack modifier) x (1 + stat modifier).

    Affinity only counts against: element_disadvantage_coef on a disadvantage. The hit bonus is the attacker's C.DMG
    (plus the damage context's CriticalDamageModifier) on a crit, crushing_hit_coef on a crushing hit, glancing_hit_coef
    on a glancing hit (HitTypeBonus, 0x182D877D0). The counterattack modifier is DamageContext.CounterattackModifier, set
    only on counterattacks.
    """
    affinity = constants["element_disadvantage_coef"] if relation == -1 else 0.0
    bonus = {CRITICAL: critical_damage, CRUSHING: constants["crushing_hit_coef"], GLANCING: constants["glancing_hit_coef"]}.get(hit, 0.0)
    return (1 + book_bonus) * (1 + affinity) * (1 + bonus) * (1 + counterattack) * (1 + stat_modifier)


def status_reduction(statuses: set[str], aoe: bool, kind: str, factors: dict[str, dict[str, float]]) -> tuple[float, list[str]]:
    """DamageCalculator.DamageReductionByStatusEffects (0x182D86DA0): the target's damage-reducing statuses.

    Starts at 1, subtracts 0.25 for Freeze and, for AoE damage, 0.075 for Invisible1 and 0.15 for Invisible2; then
    multiplies by the Stone Skin and Petrification factors, which are static data on those statuses' own effect types
    (`factors`: status name -> damage_factors from the knowledge file). The factor used depends on the damage's kind:
    TimeBomb, AoEContinuousDamage (HP Burn, Stone Skin only), or the general one. Returns the factor and the names of
    statuses whose factor the knowledge file didn't carry (left at 1).
    """
    total = 1.0
    if "Freeze" in statuses:
        total -= FREEZE_REDUCTION
    if aoe:
        total -= sum(share for name, share in INVISIBLE_AOE_REDUCTION.items() if name in statuses)
    missing = []
    stone = next((name for name in ("SimpleStoneSkin", "ReflectiveStoneSkin") if name in statuses), None)
    if stone:
        key = {"TimeBomb": "stone_skin_time_bomb", "AoEContinuousDamage": "stone_skin_hp_burn"}.get(kind, "stone_skin")
        value = (factors.get(stone) or {}).get(key)
        total *= value if value is not None else 1.0
        if value is None:
            missing.append(stone)
    if "Petrification" in statuses:
        key = "petrification_time_bomb" if kind == "TimeBomb" else "petrification"
        value = (factors.get("Petrification") or {}).get(key)
        total *= value if value is not None else 1.0
        if value is None:
            missing.append("Petrification")
    return total, missing


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
