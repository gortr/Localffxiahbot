-- ============================================================
-- LandSandBoat Auction House Economy Census
-- READ-ONLY with session-scoped temporary tables.
-- No persistent LSB data is modified.
-- ============================================================

SELECT '==============================================' AS '';
SELECT ' LSB AUCTION HOUSE ECONOMY CENSUS' AS '';
SELECT '==============================================' AS '';

SELECT VERSION() AS mariadb_version;

-- ------------------------------------------------------------
-- Build session-local working sets
-- ------------------------------------------------------------

DROP TEMPORARY TABLE IF EXISTS census_candidates;
DROP TEMPORARY TABLE IF EXISTS census_craft_outputs;
DROP TEMPORARY TABLE IF EXISTS census_craft_inputs;
DROP TEMPORARY TABLE IF EXISTS census_drop_stats;
DROP TEMPORARY TABLE IF EXISTS census_fish;

-- Initial candidate definition:
--   has an assigned AH category
--   is not GM-only
--   is not NoAuction
--   is not Exclusive
--
-- Rare is intentionally NOT excluded.
CREATE TEMPORARY TABLE census_candidates AS
SELECT
    itemid,
    name,
    sortname,
    type,
    stackSize,
    flags,
    aH,
    BaseSell,
    (flags & 0x00000002) <> 0 AS is_gm_only,
    (flags & 0x00000040) <> 0 AS is_no_auction,
    (flags & 0x00000080) <> 0 AS is_scroll,
    (flags & 0x00001000) <> 0 AS is_no_sale,
    (flags & 0x00004000) <> 0 AS is_exclusive,
    (flags & 0x00008000) <> 0 AS is_rare
FROM item_basic
WHERE
    itemid > 0
    AND aH <> 99
    AND (flags & 0x00000002) = 0
    AND (flags & 0x00000040) = 0
    AND (flags & 0x00004000) = 0;

-- All non-desynthesis craft outputs, including HQ variants.
CREATE TEMPORARY TABLE census_craft_outputs AS
SELECT DISTINCT itemid
FROM
(
    SELECT `Result` AS itemid
    FROM synth_recipes
    WHERE Desynth = 0 AND `Result` > 0

    UNION

    SELECT ResultHQ1
    FROM synth_recipes
    WHERE Desynth = 0 AND ResultHQ1 > 0

    UNION

    SELECT ResultHQ2
    FROM synth_recipes
    WHERE Desynth = 0 AND ResultHQ2 > 0

    UNION

    SELECT ResultHQ3
    FROM synth_recipes
    WHERE Desynth = 0 AND ResultHQ3 > 0
) x;

-- All materials consumed by normal synthesis.
-- Crystals are deliberately included.
CREATE TEMPORARY TABLE census_craft_inputs AS
SELECT DISTINCT itemid
FROM
(
    SELECT Crystal AS itemid
    FROM synth_recipes
    WHERE Desynth = 0 AND Crystal > 0

    UNION

    SELECT HQCrystal
    FROM synth_recipes
    WHERE Desynth = 0 AND HQCrystal > 0

    UNION

    SELECT Ingredient1
    FROM synth_recipes
    WHERE Desynth = 0 AND Ingredient1 > 0

    UNION

    SELECT Ingredient2
    FROM synth_recipes
    WHERE Desynth = 0 AND Ingredient2 > 0

    UNION

    SELECT Ingredient3
    FROM synth_recipes
    WHERE Desynth = 0 AND Ingredient3 > 0

    UNION

    SELECT Ingredient4
    FROM synth_recipes
    WHERE Desynth = 0 AND Ingredient4 > 0

    UNION

    SELECT Ingredient5
    FROM synth_recipes
    WHERE Desynth = 0 AND Ingredient5 > 0

    UNION

    SELECT Ingredient6
    FROM synth_recipes
    WHERE Desynth = 0 AND Ingredient6 > 0

    UNION

    SELECT Ingredient7
    FROM synth_recipes
    WHERE Desynth = 0 AND Ingredient7 > 0

    UNION

    SELECT Ingredient8
    FROM synth_recipes
    WHERE Desynth = 0 AND Ingredient8 > 0
) x;

-- Monster-source information.
-- itemRate in current LSB uses 1000 = 100%.
CREATE TEMPORARY TABLE census_drop_stats AS
SELECT
    d.itemId AS itemid,

    MIN(
        CASE
            WHEN d.dropType = 0 AND d.itemRate > 0
            THEN d.itemRate
            ELSE NULL
        END
    ) AS min_normal_drop_rate,

    MAX(
        CASE
            WHEN d.dropType = 0
            THEN d.itemRate
            ELSE NULL
        END
    ) AS max_normal_drop_rate,

    COUNT(DISTINCT d.dropId) AS drop_lists,

    COUNT(
        DISTINCT CONCAT(
            COALESCE(g.zoneid, 0),
            ':',
            COALESCE(g.groupid, 0)
        )
    ) AS mob_groups

FROM mob_droplist d

LEFT JOIN mob_groups g
    ON g.dropid = d.dropId

WHERE d.itemId > 0

GROUP BY d.itemId;

-- Active fishing sources.
CREATE TEMPORARY TABLE census_fish AS
SELECT
    fishid AS itemid,
    MAX(legendary) AS legendary,
    MAX(quest_only) AS quest_only
FROM fishing_fish
WHERE disabled = 0
GROUP BY fishid;

-- ============================================================
-- 1. ITEM DATABASE OVERVIEW
-- ============================================================

SELECT '--- ITEM DATABASE OVERVIEW ---' AS '';

SELECT
    COUNT(*) AS total_item_basic_rows,

    SUM(aH <> 99) AS ah_category_assigned,

    SUM((flags & 0x00000040) <> 0) AS noauction_items,

    SUM((flags & 0x00004000) <> 0) AS exclusive_items,

    SUM((flags & 0x00008000) <> 0) AS rare_items,

    SUM((flags & 0x00000002) <> 0) AS gmonly_items,

    SUM(
        itemid > 0
        AND aH <> 99
        AND (flags & 0x00000002) = 0
        AND (flags & 0x00000040) = 0
        AND (flags & 0x00004000) = 0
    ) AS initial_market_candidates

FROM item_basic;

-- ============================================================
-- 2. MARKET CANDIDATES BY ITEM TYPE
-- ============================================================

SELECT '--- CANDIDATES BY TYPE ---' AS '';

SELECT
    type,

    CASE type
        WHEN 0   THEN 'BASIC'
        WHEN 1   THEN 'GENERAL'
        WHEN 2   THEN 'USABLE'
        WHEN 4   THEN 'PUPPET'
        WHEN 8   THEN 'EQUIPMENT'
        WHEN 16  THEN 'WEAPON'
        WHEN 32  THEN 'CURRENCY'
        WHEN 64  THEN 'FURNISHING'
        WHEN 128 THEN 'LINKSHELL'
        ELSE CONCAT('OTHER_', type)
    END AS type_name,

    COUNT(*) AS item_count

FROM census_candidates

GROUP BY type
ORDER BY type;

-- ============================================================
-- 3. AUCTION HOUSE CATEGORY DISTRIBUTION
-- ============================================================

SELECT '--- CANDIDATES BY AH CATEGORY ---' AS '';

SELECT
    aH AS ah_category,
    COUNT(*) AS item_count
FROM census_candidates
GROUP BY aH
ORDER BY aH;

-- ============================================================
-- 4. STACK SIZE DISTRIBUTION
-- ============================================================

SELECT '--- CANDIDATES BY STACK SIZE ---' AS '';

SELECT
    stackSize,
    COUNT(*) AS item_count
FROM census_candidates
GROUP BY stackSize
ORDER BY stackSize;

-- ============================================================
-- 5. FLAG PROFILE INSIDE OTHERWISE-VALID AH ITEMS
-- ============================================================

SELECT '--- CANDIDATE FLAG PROFILE ---' AS '';

SELECT
    COUNT(*) AS candidate_items,

    SUM(is_rare) AS rare,

    SUM(is_scroll) AS scrolls,

    SUM(is_no_sale) AS cannot_sell_to_npc,

    SUM(BaseSell = 0) AS zero_base_sell,

    SUM(BaseSell > 0) AS positive_base_sell

FROM census_candidates;

-- ============================================================
-- 6. RECIPE DATABASE
-- ============================================================

SELECT '--- SYNTHESIS DATABASE ---' AS '';

SELECT
    COUNT(*) AS recipes_total,
    SUM(Desynth = 0) AS normal_synth_recipes,
    SUM(Desynth <> 0) AS desynth_recipes,
    COUNT(DISTINCT CASE WHEN Desynth = 0 THEN `Result` END)
        AS distinct_normal_results
FROM synth_recipes;

-- ============================================================
-- 7. SOURCE / PROVENANCE COVERAGE
-- ============================================================

SELECT '--- INITIAL PROVENANCE COVERAGE ---' AS '';

SELECT
    COUNT(*) AS candidates,

    SUM(co.itemid IS NOT NULL) AS craft_outputs,

    SUM(ci.itemid IS NOT NULL) AS craft_materials,

    SUM(ds.itemid IS NOT NULL) AS monster_sources,

    SUM(f.itemid IS NOT NULL) AS fishing_sources,

    SUM(
        co.itemid IS NULL
        AND ci.itemid IS NULL
        AND ds.itemid IS NULL
        AND f.itemid IS NULL
    ) AS unknown_to_db_census

FROM census_candidates c

LEFT JOIN census_craft_outputs co
    ON co.itemid = c.itemid

LEFT JOIN census_craft_inputs ci
    ON ci.itemid = c.itemid

LEFT JOIN census_drop_stats ds
    ON ds.itemid = c.itemid

LEFT JOIN census_fish f
    ON f.itemid = c.itemid;

-- ============================================================
-- 8. EQUIPMENT / WEAPON PROFILE
-- ============================================================

SELECT '--- EQUIPMENT AND WEAPONS ---' AS '';

SELECT
    COUNT(*) AS equipment_weapon_candidates,

    SUM(co.itemid IS NOT NULL)
        AS craftable,

    SUM(co.itemid IS NULL)
        AS not_known_craftable,

    SUM(co.itemid IS NULL AND ds.itemid IS NOT NULL)
        AS noncraftable_with_monster_source,

    SUM(c.is_rare)
        AS rare_equipment_or_weapons

FROM census_candidates c

LEFT JOIN census_craft_outputs co
    ON co.itemid = c.itemid

LEFT JOIN census_drop_stats ds
    ON ds.itemid = c.itemid

WHERE (c.type & 24) <> 0;

-- ============================================================
-- 9. STACKABLE MARKET PROFILE
-- ============================================================

SELECT '--- STACKABLE ITEMS ---' AS '';

SELECT
    COUNT(*) AS stackable_candidates,

    SUM(ci.itemid IS NOT NULL)
        AS used_in_synthesis,

    SUM(co.itemid IS NOT NULL)
        AS produced_by_synthesis,

    SUM(ds.itemid IS NOT NULL)
        AS monster_sourced,

    SUM(f.itemid IS NOT NULL)
        AS fishable

FROM census_candidates c

LEFT JOIN census_craft_inputs ci
    ON ci.itemid = c.itemid

LEFT JOIN census_craft_outputs co
    ON co.itemid = c.itemid

LEFT JOIN census_drop_stats ds
    ON ds.itemid = c.itemid

LEFT JOIN census_fish f
    ON f.itemid = c.itemid

WHERE c.stackSize > 1;

-- ============================================================
-- 10. RARE BUT AH-CANDIDATE ITEMS
-- ============================================================

SELECT '--- SAMPLE: RARE AH CANDIDATES ---' AS '';

SELECT
    c.itemid,
    c.name,
    c.type,
    c.stackSize,
    c.aH,
    c.BaseSell,

    IF(co.itemid IS NULL, 'N', 'Y') AS craft_output,
    IF(ci.itemid IS NULL, 'N', 'Y') AS craft_material,
    IF(ds.itemid IS NULL, 'N', 'Y') AS monster_source,
    IF(f.itemid IS NULL, 'N', 'Y') AS fishing_source,

    ds.min_normal_drop_rate,
    ds.max_normal_drop_rate,
    ds.drop_lists,
    ds.mob_groups

FROM census_candidates c

LEFT JOIN census_craft_outputs co
    ON co.itemid = c.itemid

LEFT JOIN census_craft_inputs ci
    ON ci.itemid = c.itemid

LEFT JOIN census_drop_stats ds
    ON ds.itemid = c.itemid

LEFT JOIN census_fish f
    ON f.itemid = c.itemid

WHERE c.is_rare = 1

ORDER BY
    (c.type & 24) DESC,
    c.name

LIMIT 60;

-- ============================================================
-- 11. NONCRAFTABLE EQUIPMENT WITH MONSTER SOURCES
-- These are prime DEMAND_ONLY candidates.
-- ============================================================

SELECT '--- SAMPLE: NONCRAFTABLE MONSTER-SOURCED EQUIPMENT ---' AS '';

SELECT
    c.itemid,
    c.name,
    c.type,
    c.is_rare,
    c.aH,
    c.BaseSell,
    ds.min_normal_drop_rate,
    ds.max_normal_drop_rate,
    ds.drop_lists,
    ds.mob_groups

FROM census_candidates c

LEFT JOIN census_craft_outputs co
    ON co.itemid = c.itemid

JOIN census_drop_stats ds
    ON ds.itemid = c.itemid

WHERE
    (c.type & 24) <> 0
    AND co.itemid IS NULL

ORDER BY
    c.is_rare DESC,
    ds.min_normal_drop_rate ASC,
    c.name

LIMIT 80;

-- ============================================================
-- 12. UNKNOWN-PROVENANCE ITEMS
-- These default to REVIEW / no synthetic supply.
-- ============================================================

SELECT '--- SAMPLE: UNKNOWN PROVENANCE ---' AS '';

SELECT
    c.itemid,
    c.name,
    c.type,
    c.stackSize,
    c.is_rare,
    c.is_scroll,
    c.aH,
    c.BaseSell

FROM census_candidates c

LEFT JOIN census_craft_outputs co
    ON co.itemid = c.itemid

LEFT JOIN census_craft_inputs ci
    ON ci.itemid = c.itemid

LEFT JOIN census_drop_stats ds
    ON ds.itemid = c.itemid

LEFT JOIN census_fish f
    ON f.itemid = c.itemid

WHERE
    co.itemid IS NULL
    AND ci.itemid IS NULL
    AND ds.itemid IS NULL
    AND f.itemid IS NULL

ORDER BY
    c.type,
    c.is_rare DESC,
    c.name

LIMIT 80;

-- ============================================================
-- 13. ITEMS WITH NO AH CATEGORY BUT WITHOUT HARD FLAGS
-- This helps us verify what aH=99 actually represents locally.
-- ============================================================

SELECT '--- SAMPLE: AH CATEGORY 99 WITHOUT HARD BLOCK FLAGS ---' AS '';

SELECT
    itemid,
    name,
    type,
    stackSize,
    flags,
    BaseSell

FROM item_basic

WHERE
    itemid > 0
    AND aH = 99
    AND (flags & 0x00000002) = 0
    AND (flags & 0x00000040) = 0
    AND (flags & 0x00004000) = 0

ORDER BY type, name

LIMIT 50;

SELECT '==============================================' AS '';
SELECT ' END OF ECONOMY CENSUS' AS '';
SELECT '==============================================' AS '';
