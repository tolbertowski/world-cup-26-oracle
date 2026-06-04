"""Streamlit entrypoint for the World Cup Oracle app."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px

from world_cup_oracle.data import build_demo_fixtures, build_demo_teams
from world_cup_oracle.data.io import (
    apply_team_adjustments,
    read_match_updates,
    read_team_adjustments,
)
from world_cup_oracle.domain import Fixture, MatchPrediction, Team
from world_cup_oracle.models import MatchPredictor
from world_cup_oracle.simulation import run_monte_carlo


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
    teams, fixtures, predictor, locked_results = _load_demo_context()
    team_names = {team.code: team.name for team in teams}

    with st.sidebar:
        st.header("World Cup 26 Oracle")
        page = st.radio(
            "View",
            ["Dashboard", "Match Predictor", "Tournament Simulator", "Model Check", "Data Update"],
            label_visibility="collapsed",
        )
        simulations = st.slider("Simulations", min_value=100, max_value=3000, value=750, step=100)
        seed = st.number_input("Seed", min_value=1, max_value=9999, value=26, step=1)

    if page == "Dashboard":
        _dashboard(st, teams, fixtures, predictor, simulations, seed, team_names, locked_results)
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


def _load_demo_context() -> tuple[list[Team], list[Fixture], MatchPredictor, dict]:
    teams = build_demo_teams()
    fixtures = build_demo_fixtures()
    predictor = MatchPredictor.from_teams(teams)
    adjustments = read_team_adjustments(ROOT / "data" / "manual" / "team_adjustments.csv")
    predictor = MatchPredictor(apply_team_adjustments(predictor.ratings, adjustments))
    locked_results = read_match_updates(ROOT / "data" / "manual" / "match_updates.csv")
    return teams, fixtures, predictor, locked_results


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
        </style>
        """,
        unsafe_allow_html=True,
    )
