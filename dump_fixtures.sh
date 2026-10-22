#!/bin/bash
set -e

# Snapshots the current database's content tables into the fixture JSON files.
#
# This is a ONE-WAY tool. There is deliberately no loading counterpart: the
# seeding script was deleted because `loaddata` upserts by primary key, and
# production content lives in admin-panel edits that a stale snapshot would
# silently overwrite. Treat these files as a diffable backup only — never load
# them into a database you care about. (`manage.py pull_prod_content` is how a
# local database gets real content.)
#
# Usage: bash dump_fixtures.sh   (run from the backend/ directory)
#
# Output is normalized to match the repo's fixture style — 2-space indent, raw
# UTF-8 (not \uXXXX escapes), trailing newline — so diffs show only real data
# changes rather than reformatting. Review `git diff` before committing.

F=calculatorapi/fixtures

# Dump one model to a fixture file in the repo's canonical format.
dump() {
    local model="$1" out="$2"
    python3 manage.py dumpdata "$model" | python3 -c "
import sys, json
data = json.load(sys.stdin)
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
" "$out"
    echo "  wrote $out"
}

echo "Dumping fixtures..."

# Rank tables
dump calculatorapi.ClubRank             $F/clubRanks.json
dump calculatorapi.TeamTrialsRank       $F/teamTrialsRanks.json
dump calculatorapi.ChampionsMeetingRank $F/championsMeetingRanks.json
dump calculatorapi.LeagueOfHeroesRank   $F/leagueOfHeroesRanks.json

# Core content tables
dump calculatorapi.Uma                  $F/umas.json
dump calculatorapi.SupportCard          $F/supportCards.json
dump calculatorapi.BannerTimeline       $F/bannerTimelines.json
dump calculatorapi.ChampionsMeeting     $F/championsMeetings.json
dump calculatorapi.LeagueOfHeroes       $F/leagueOfHeroes.json
dump calculatorapi.GameEvent            $F/gameEvents.json
dump calculatorapi.ChangelogEntry       $F/changelogEntries.json

# Models with FK dependencies
dump calculatorapi.BannerUma            $F/bannerUmas.json
dump calculatorapi.BannerSupport        $F/bannerSupports.json
dump calculatorapi.ChangelogChange      $F/changelogChanges.json

# M2M through tables
dump calculatorapi.UmasOnUmaBanner          $F/umasOnUmaBanner.json
dump calculatorapi.SupportsOnSupportBanner  $F/supportsOnSupportBanner.json

echo "Fixtures dumped."
