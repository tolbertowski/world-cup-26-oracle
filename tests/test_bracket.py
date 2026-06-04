from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.domain import Fixture, MatchResult, MatchStage
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.simulation import knockout_advance_share, project_bracket


def _context():
    teams = build_demo_teams()
    fixtures = build_demo_fixtures()
    predictor = MatchPredictor.from_teams(teams)
    return teams, fixtures, predictor


def test_projected_bracket_has_expected_round_sizes() -> None:
    teams, fixtures, predictor = _context()
    bracket = project_bracket(teams, fixtures, predictor)

    sizes = {stage: len(matches) for stage, matches in bracket.rounds}
    assert sizes[MatchStage.ROUND_OF_32] == 16
    assert sizes[MatchStage.ROUND_OF_16] == 8
    assert sizes[MatchStage.QUARTER_FINAL] == 4
    assert sizes[MatchStage.SEMI_FINAL] == 2
    assert sizes[MatchStage.FINAL] == 1
    assert sizes[MatchStage.THIRD_PLACE] == 1
    assert bracket.champion
    assert bracket.third_place


def test_projected_bracket_is_deterministic() -> None:
    teams, fixtures, predictor = _context()
    first = project_bracket(teams, fixtures, predictor)
    second = project_bracket(teams, fixtures, predictor)

    assert first.champion == second.champion
    assert first.third_place == second.third_place
    assert [m.match_id for _, r in first.rounds for m in r] == [
        m.match_id for _, r in second.rounds for m in r
    ]
    assert [m.projected_winner for _, r in first.rounds for m in r] == [
        m.projected_winner for _, r in second.rounds for m in r
    ]


def test_champion_and_advancers_come_from_round_of_32() -> None:
    teams, fixtures, predictor = _context()
    bracket = project_bracket(teams, fixtures, predictor)

    r32 = next(matches for stage, matches in bracket.rounds if stage == MatchStage.ROUND_OF_32)
    participants = {team for match in r32 for team in (match.home_team, match.away_team)}

    assert bracket.champion in participants
    for _, matches in bracket.rounds:
        for match in matches:
            assert match.projected_winner in (match.home_team, match.away_team)


def test_locked_group_result_drives_qualifiers() -> None:
    teams, fixtures, predictor = _context()
    group_a = [fixture for fixture in fixtures if fixture.group == "A"]
    chosen = group_a[0].home_team

    locked: dict[str, MatchResult] = {}
    for fixture in group_a:
        if fixture.home_team == chosen:
            locked[fixture.match_id] = MatchResult(fixture.match_id, 3, 0)
        elif fixture.away_team == chosen:
            locked[fixture.match_id] = MatchResult(fixture.match_id, 0, 3)
        else:
            locked[fixture.match_id] = MatchResult(fixture.match_id, 0, 0)

    bracket = project_bracket(teams, fixtures, predictor, locked)
    r32 = next(matches for stage, matches in bracket.rounds if stage == MatchStage.ROUND_OF_32)
    # R32-01 pairs the Group A winner ("1A") as the home side.
    group_a_slot = next(match for match in r32 if match.match_id == "R32-01")
    assert group_a_slot.home_team == chosen


def test_locked_knockout_result_flips_projected_winner() -> None:
    teams, fixtures, predictor = _context()
    baseline = project_bracket(teams, fixtures, predictor)
    target = next(matches for stage, matches in baseline.rounds if stage == MatchStage.ROUND_OF_32)[0]
    underdog = target.away_team if target.projected_winner == target.home_team else target.home_team

    # Lock the same R32 tie so the projected loser wins instead.
    if underdog == target.home_team:
        locked = {target.match_id: MatchResult(target.match_id, 1, 0)}
    else:
        locked = {target.match_id: MatchResult(target.match_id, 0, 1)}

    flipped = project_bracket(teams, fixtures, predictor, locked)
    flipped_match = next(
        match
        for stage, matches in flipped.rounds
        if stage == MatchStage.ROUND_OF_32
        for match in matches
        if match.match_id == target.match_id
    )
    assert flipped_match.projected_winner == underdog
    assert flipped_match.source == "locked"


def test_knockout_advance_share_is_a_probability_that_favors_the_stronger_side() -> None:
    teams, fixtures, predictor = _context()
    ranked = sorted(teams, key=lambda team: predictor.ratings[team.code].rating)
    weak, strong = ranked[0], ranked[-1]

    strong_home = predictor.predict(Fixture("X1", MatchStage.ROUND_OF_32, strong.code, weak.code))
    strong_away = predictor.predict(Fixture("X2", MatchStage.ROUND_OF_32, weak.code, strong.code))

    assert 0.0 <= knockout_advance_share(strong_home) <= 1.0
    assert 0.0 <= knockout_advance_share(strong_away) <= 1.0
    assert knockout_advance_share(strong_home) > 0.5
    assert knockout_advance_share(strong_away) < 0.5
