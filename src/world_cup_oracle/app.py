"""Streamlit entrypoint for the World Cup Oracle app."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px

from world_cup_oracle.data import load_processed_or_demo
from world_cup_oracle.data.io import (
    apply_team_adjustments,
    read_match_updates,
    read_team_adjustments,
)
from world_cup_oracle.domain import Fixture, MatchPrediction, MatchStage, Team
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.simulation import project_bracket, run_monte_carlo


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    try:
        import streamlit as st
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised outside tests
        raise RuntimeError(
            "Streamlit is required to run the app. Install project dependencies first."
        ) from exc

    st.set_page_config(
        page_title="World Cup 26 Oracle",
        page_icon="WC",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_css(st)
    teams, fixtures, predictor, locked_results, source = _load_tournament_context()
    team_names = {team.code: team.name for team in teams}

    with st.sidebar:
        st.header("World Cup 26 Oracle")
        page = st.radio(
            "View",
            ["Dashboard", "Fixtures", "Bracket", "Match Predictor", "Tournament Simulator", "Model Check", "Data Update"],
            label_visibility="collapsed",
        )
        simulations = st.slider("Simulations", min_value=100, max_value=3000, value=750, step=100)
        seed = st.number_input("Seed", min_value=1, max_value=9999, value=26, step=1)
        st.caption(f"Data source: {source}")

    if page == "Dashboard":
        _dashboard(st, teams, fixtures, predictor, simulations, seed, team_names, locked_results)
    elif page == "Fixtures":
        _fixtures_view(st, fixtures, team_names)
    elif page == "Bracket":
        _bracket_view(st, teams, fixtures, predictor, team_names, locked_results)
    elif page == "Match Predictor":
        _match_predictor(st, fixtures, predictor, team_names)
    elif page == "Tournament Simulator":
        _tournament_simulator(st, teams, fixtures, predictor, simulations, seed, team_names, locked_results)
    elif page == "Model Check":
        _model_check(st, teams, predictor)
    else:
        _data_update(st)


def _dashboard(
    st,
    teams: list[Team],
    fixtures: list[Fixture],
    predictor: MatchPredictor,
    simulations: int,
    seed: int,
    team_names: dict[str, str],
    locked_results: dict,
) -> None:
    st.title("World Cup 26 Oracle")
    st.caption("Match odds, scorelines, cards, corners, upsets, and bracket chaos.")
    summary = _simulate(teams, fixtures, predictor, simulations, seed, locked_results)
    champion_rows = _probability_rows(summary.champion_probs, team_names).head(12)
    finalist_rows = _probability_rows(summary.finalist_probs, team_names).head(12)
    upset_rows = _probability_rows(summary.upset_probs, {}).head(8)

    col1, col2, col3, col4 = st.columns(4)
    top_champion = champion_rows.iloc[0]
    col1.metric("Title favorite", top_champion["Label"], f"{top_champion['Probability']:.1%}")
    col2.metric("Simulations", f"{summary.simulations:,}")
    col3.metric("Teams", f"{len(teams)}")
    col4.metric("Group matches", f"{len(fixtures)}")

    left, right = st.columns([1.35, 1.0])
    with left:
        st.subheader("Champion Odds")
        st.plotly_chart(
            px.bar(
                champion_rows.sort_values("Probability"),
                x="Probability",
                y="Label",
                orientation="h",
                color="Probability",
                color_continuous_scale="Tealgrn",
                range_x=[0, max(0.05, champion_rows["Probability"].max() * 1.15)],
            ),
            use_container_width=True,
        )
    with right:
        st.subheader("Finalist Odds")
        st.dataframe(
            finalist_rows,
            use_container_width=True,
            hide_index=True,
        )
        st.subheader("Upset Watch")
        st.dataframe(upset_rows, use_container_width=True, hide_index=True)


def _fixtures_view(st, fixtures: list[Fixture], team_names: dict[str, str]) -> None:
    st.title("Fixtures")
    rows = _fixture_rows(fixtures, team_names)
    groups = ["All", *sorted(group for group in rows["Group"].dropna().unique() if group)]
    selected_group = st.selectbox("Group", groups)
    search = st.text_input("Team or venue", placeholder="Search team, code, venue, or match ID")

    filtered = rows
    if selected_group != "All":
        filtered = filtered[filtered["Group"] == selected_group]
    if search:
        query = search.casefold()
        mask = filtered.apply(lambda row: query in " ".join(str(value) for value in row).casefold(), axis=1)
        filtered = filtered[mask]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Fixtures", len(filtered))
    col2.metric("Groups", rows["Group"].nunique())
    col3.metric("Venues", rows["Venue"].nunique())
    col4.metric("Source", "FIFA")
    st.dataframe(filtered, use_container_width=True, hide_index=True)


_BRACKET_ROUND_LABELS = {
    MatchStage.ROUND_OF_32: "Round of 32",
    MatchStage.ROUND_OF_16: "Round of 16",
    MatchStage.QUARTER_FINAL: "Quarter-finals",
    MatchStage.SEMI_FINAL: "Semi-finals",
    MatchStage.FINAL: "Final",
    MatchStage.THIRD_PLACE: "Third place",
}


def _bracket_view(
    st,
    teams: list[Team],
    fixtures: list[Fixture],
    predictor: MatchPredictor,
    team_names: dict[str, str],
    locked_results: dict,
) -> None:
    st.title("Projected Bracket")
    st.caption(
        "The model's single most-likely path: deterministic group standings and "
        "knockout winners, all the way to the champion. Not betting advice."
    )

    bracket = project_bracket(teams, fixtures, predictor, locked_results)
    st.success(f"Projected champion: {team_names.get(bracket.champion, bracket.champion)}")
    if bracket.third_place:
        st.caption(f"Projected third place: {team_names.get(bracket.third_place, bracket.third_place)}")

    knockout_rounds = [
        (stage, matches)
        for stage, matches in bracket.rounds
        if stage != MatchStage.THIRD_PLACE
    ]
    columns = st.columns(len(knockout_rounds) + 1)
    for column, (stage, matches) in zip(columns, knockout_rounds):
        cards = "".join(_bracket_card_html(match, team_names) for match in matches)
        title = _BRACKET_ROUND_LABELS.get(stage, stage.value)
        column.markdown(
            f'<div class="wc-round-title">{title}</div><div class="wc-col">{cards}</div>',
            unsafe_allow_html=True,
        )

    champion = team_names.get(bracket.champion, bracket.champion)
    champion_card = (
        f'<div class="wc-champion"><div class="wc-trophy">&#127942;</div>{_html_escape(champion)}</div>'
    )
    columns[-1].markdown(
        f'<div class="wc-round-title">Champion</div><div class="wc-col">{champion_card}</div>',
        unsafe_allow_html=True,
    )

    st.subheader("Bracket Detail")
    st.dataframe(_bracket_rows(bracket, team_names), use_container_width=True, hide_index=True)


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _bracket_card_html(match, team_names: dict[str, str]) -> str:
    home = _html_escape(team_names.get(match.home_team, match.home_team))
    away = _html_escape(team_names.get(match.away_team, match.away_team))
    home_win = match.projected_winner == match.home_team
    tag = "locked" if match.source == "locked" else f"{match.advance_prob:.0%}"
    home_tag = f'<span class="wc-tag">{tag}</span>' if home_win else ""
    away_tag = f'<span class="wc-tag">{tag}</span>' if not home_win else ""
    home_cls = "wc-team wc-win" if home_win else "wc-team"
    away_cls = "wc-team wc-win" if not home_win else "wc-team"
    return (
        '<div class="wc-match">'
        f'<div class="{home_cls}"><span class="wc-name">{home}</span>{home_tag}</div>'
        f'<div class="{away_cls}"><span class="wc-name">{away}</span>{away_tag}</div>'
        "</div>"
    )


def _bracket_rows(bracket, team_names: dict[str, str]) -> pd.DataFrame:
    rows = []
    for stage, matches in bracket.rounds:
        for match in matches:
            rows.append(
                {
                    "Round": _BRACKET_ROUND_LABELS.get(stage, stage.value),
                    "Home": team_names.get(match.home_team, match.home_team),
                    "Away": team_names.get(match.away_team, match.away_team),
                    "Projected Winner": team_names.get(match.projected_winner, match.projected_winner),
                    "Advance %": "locked" if match.source == "locked" else f"{match.advance_prob:.0%}",
                }
            )
    return pd.DataFrame(rows)


def _match_predictor(st, fixtures: list[Fixture], predictor: MatchPredictor, team_names: dict[str, str]) -> None:
    st.title("Match Predictor")
    fixture_lookup = {
        f"{fixture.group or fixture.stage.value.upper()} | {team_names.get(fixture.home_team, fixture.home_team)} vs {team_names.get(fixture.away_team, fixture.away_team)}": fixture
        for fixture in fixtures
    }
    selected = st.selectbox("Fixture", list(fixture_lookup))
    fixture = fixture_lookup[selected]
    prediction = predictor.predict(fixture)

    _prediction_metrics(st, prediction, team_names)
    left, right = st.columns([1.1, 1.0])
    with left:
        st.subheader("Likely Scorelines")
        score_rows = _scoreline_rows(prediction).head(10)
        st.dataframe(score_rows, use_container_width=True, hide_index=True)
    with right:
        st.subheader("Method")
        method_rows = pd.DataFrame(
            [
                {"Method": method.value.replace("_", " ").title(), "Probability": probability}
                for method, probability in prediction.method_probs.items()
            ]
        )
        st.dataframe(method_rows, use_container_width=True, hide_index=True)
        st.subheader("Model Read")
        for item in prediction.explanation:
            st.write(item)


def _tournament_simulator(
    st,
    teams: list[Team],
    fixtures: list[Fixture],
    predictor: MatchPredictor,
    simulations: int,
    seed: int,
    team_names: dict[str, str],
    locked_results: dict,
) -> None:
    st.title("Tournament Simulator")
    summary = _simulate(teams, fixtures, predictor, simulations, seed, locked_results)

    groups = sorted(summary.group_winner_probs)
    selected_group = st.segmented_control("Group", groups, default=groups[0])
    group_rows = _probability_rows(summary.group_winner_probs[selected_group], team_names)

    left, right = st.columns([1.0, 1.2])
    with left:
        st.subheader(f"Group {selected_group} Winner")
        st.dataframe(group_rows, use_container_width=True, hide_index=True)
        st.subheader("Knockout Qualification")
        st.dataframe(_probability_rows(summary.knockout_probs, team_names).head(16), use_container_width=True, hide_index=True)
    with right:
        st.subheader("Title Race")
        champion_rows = _probability_rows(summary.champion_probs, team_names).head(16)
        st.plotly_chart(
            px.bar(
                champion_rows,
                x="Label",
                y="Probability",
                color="Probability",
                color_continuous_scale="Emrld",
            ),
            use_container_width=True,
        )


def _model_check(st, teams: list[Team], predictor: MatchPredictor) -> None:
    st.title("Model Check")
    rating_rows = pd.DataFrame(
        [
            {
                "Team": team.name,
                "Code": team.code,
                "Group": team.group,
                "Rating": predictor.ratings[team.code].rating,
                "Attack": predictor.ratings[team.code].attack,
                "Defense": predictor.ratings[team.code].defense,
                "Tempo": predictor.ratings[team.code].tempo,
                "Discipline": predictor.ratings[team.code].discipline,
            }
            for team in teams
        ]
    ).sort_values("Rating", ascending=False)
    col1, col2, col3 = st.columns(3)
    col1.metric("Average total goals", "2.62")
    col2.metric("Scoreline grid", "0-7")
    col3.metric("Calibration", "Backtest-ready")
    st.dataframe(rating_rows, use_container_width=True, hide_index=True)


def _data_update(st) -> None:
    st.title("Data Update")
    st.caption("Manual-assisted workflow for tournament results and model adjustments.")
    updates = read_match_updates(ROOT / "data" / "manual" / "match_updates.csv")
    adjustments = read_team_adjustments(ROOT / "data" / "manual" / "team_adjustments.csv")
    col1, col2 = st.columns(2)
    col1.metric("Locked matches", len(updates))
    col2.metric("Team adjustments", len(adjustments))
    st.subheader("Tracked Templates")
    rows = [
        {"File": "data/manual/match_updates.csv", "Purpose": "Lock played matches, cards, corners, and notes."},
        {"File": "data/manual/team_adjustments.csv", "Purpose": "Apply manual rating, attack, defense, discipline, and tempo adjustments."},
        {"File": "data/cache/", "Purpose": "Store cached free-data snapshots before import."},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.subheader("Import Preview")
    uploaded = st.file_uploader("CSV update file", type=["csv"])
    if uploaded is not None:
        st.dataframe(pd.read_csv(uploaded), use_container_width=True)


def _simulate(
    teams: list[Team],
    fixtures: list[Fixture],
    predictor: MatchPredictor,
    simulations: int,
    seed: int,
    locked_results: dict,
):
    return run_monte_carlo(
        teams,
        fixtures,
        predictor,
        simulations=simulations,
        seed=seed,
        locked_results=locked_results,
    )


def _load_tournament_context() -> tuple[list[Team], list[Fixture], MatchPredictor, dict, str]:
    tournament_data = load_processed_or_demo(ROOT / "data" / "processed")
    teams = tournament_data.teams
    fixtures = tournament_data.fixtures
    predictor = MatchPredictor.from_teams(teams)
    adjustments = read_team_adjustments(ROOT / "data" / "manual" / "team_adjustments.csv")
    predictor = MatchPredictor(apply_team_adjustments(predictor.ratings, adjustments))
    locked_results = read_match_updates(ROOT / "data" / "manual" / "match_updates.csv")
    return teams, fixtures, predictor, locked_results, tournament_data.source


def _prediction_metrics(st, prediction: MatchPrediction, team_names: dict[str, str]) -> None:
    home_name = team_names.get(prediction.fixture.home_team, prediction.fixture.home_team)
    away_name = team_names.get(prediction.fixture.away_team, prediction.fixture.away_team)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(f"{home_name} win", f"{prediction.home_win:.1%}")
    col2.metric("Draw", f"{prediction.draw:.1%}")
    col3.metric(f"{away_name} win", f"{prediction.away_win:.1%}")
    col4.metric("Expected goals", f"{prediction.expected_home_goals:.2f}-{prediction.expected_away_goals:.2f}")
    col5, col6, col7, col8 = st.columns(4)
    col5.metric(f"{home_name} corners", f"{prediction.expected_home_corners:.1f}")
    col6.metric(f"{away_name} corners", f"{prediction.expected_away_corners:.1f}")
    col7.metric(f"{home_name} cards", f"{prediction.expected_home_cards:.1f}")
    col8.metric(f"{away_name} cards", f"{prediction.expected_away_cards:.1f}")


def _probability_rows(probs: dict[str, float], team_names: dict[str, str]) -> pd.DataFrame:
    if not probs:
        return pd.DataFrame(columns=["Label", "Code", "Probability"])
    return pd.DataFrame(
        [
            {
                "Label": team_names.get(key, key),
                "Code": key if key in team_names else "",
                "Probability": probability,
            }
            for key, probability in probs.items()
        ]
    ).sort_values("Probability", ascending=False)


def _fixture_rows(
    fixtures: list[Fixture],
    team_names: dict[str, str],
    *,
    timezone: str = "Australia/Melbourne",
) -> pd.DataFrame:
    rows = []
    for fixture in fixtures:
        kickoff = pd.to_datetime(fixture.kickoff, utc=True, errors="coerce") if fixture.kickoff else pd.NaT
        local_kickoff = "" if pd.isna(kickoff) else kickoff.tz_convert(timezone).strftime("%d %b %Y, %H:%M")
        rows.append(
            {
                "_sort_kickoff": "" if pd.isna(kickoff) else kickoff.isoformat(),
                "Match ID": fixture.match_id,
                "Group": fixture.group or "",
                "Home": team_names.get(fixture.home_team, fixture.home_team),
                "Away": team_names.get(fixture.away_team, fixture.away_team),
                "Home Code": fixture.home_team,
                "Away Code": fixture.away_team,
                "Kickoff": local_kickoff,
                "Venue": fixture.venue or "",
                "Stage": fixture.stage.value.replace("_", " ").title(),
            }
        )
    return pd.DataFrame(rows).sort_values(["_sort_kickoff", "Match ID"]).drop(columns=["_sort_kickoff"])


def _scoreline_rows(prediction: MatchPrediction) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Scoreline": f"{home}-{away}", "Probability": probability}
            for (home, away), probability in prediction.scoreline_probs.items()
        ]
    ).sort_values("Probability", ascending=False)


def _inject_css(st) -> None:
    st.markdown(
        """
        <style>
        .stMetric {
            background: #eef2ef;
            border: 1px solid #d8dfdb;
            border-radius: 8px;
            padding: 10px 12px;
        }
        div[data-testid="stSidebarContent"] {
            background: #1f3d38;
            color: #f8faf7;
        }
        div[data-testid="stSidebarContent"] label,
        div[data-testid="stSidebarContent"] p,
        div[data-testid="stSidebarContent"] span {
            color: #f8faf7;
        }
        .wc-round-title {
            font-weight: 700;
            color: #1f3d38;
            text-align: center;
            font-size: 0.9rem;
            margin-bottom: 10px;
            letter-spacing: 0.02em;
        }
        .wc-col {
            display: flex;
            flex-direction: column;
            justify-content: space-around;
            min-height: 1120px;
        }
        .wc-match {
            border: 1px solid #d8dfdb;
            border-radius: 10px;
            overflow: hidden;
            background: #ffffff;
            box-shadow: 0 1px 2px rgba(31, 61, 56, 0.06);
        }
        .wc-team {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 7px 10px;
            font-size: 0.84rem;
            color: #41514c;
            gap: 6px;
        }
        .wc-team + .wc-team {
            border-top: 1px solid #eef2ef;
        }
        .wc-win {
            background: #eef2ef;
            font-weight: 700;
            color: #1f3d38;
            box-shadow: inset 3px 0 0 #1f3d38;
        }
        .wc-name {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .wc-tag {
            font-size: 0.7rem;
            color: #5b6b66;
            font-weight: 600;
            flex-shrink: 0;
        }
        .wc-win .wc-tag {
            color: #1f3d38;
        }
        .wc-champion {
            border: 1px solid #1f3d38;
            border-radius: 10px;
            background: #1f3d38;
            color: #f8faf7;
            text-align: center;
            font-weight: 700;
            font-size: 0.9rem;
            padding: 12px 10px;
        }
        .wc-trophy {
            font-size: 1.5rem;
            line-height: 1.2;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
