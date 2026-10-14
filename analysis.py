"""Individual track results and grade-based Class 1A benchmarks.

Run: py analysis.py
Keep track_data.xlsx beside this script. The workbook is read, never modified.
Benchmarks are descriptive comparisons, not official state qualifications.
Relays are not included in these calculations.
"""

from datetime import time
from pathlib import Path
import math

import pandas as pd

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, PercentFormatter

# Settings: change these instead of commenting out code.
ANALYSIS_SEASON = 2026
HIGHLIGHT_SEASON = 2025
SAVE_CHARTS = True
SHOW_CHARTS = False
PRINT_DETAILS = False
FILE_PATH = Path(__file__).parent / "track_data.xlsx"


def require(condition, message):
    """Stop before reporting results if a necessary check fails."""
    if not condition:
        raise ValueError(message)


def parse_standard(raw, unit):
    """Independently convert StandardRaw to seconds or inches for validation."""
    if isinstance(raw, time):
        require(unit == "seconds", "A field standard contains an Excel time.")
        return raw.hour * 3600 + raw.minute * 60 + raw.second + raw.microsecond / 1e6
    mark = str(raw).replace("'", "").strip()
    if unit == "inches":
        require(mark.endswith("m"), f"Expected a meter standard, found {raw!r}.")
        return float(mark[:-1].strip()) / 0.0254
    require(unit == "seconds", f"Unknown standard unit: {unit!r}.")
    if ":" in mark:
        minutes, seconds = mark.split(":")
        return float(minutes or 0) * 60 + float(seconds)
    return float(mark)


# 1. Load the workbook and normalize the agreed event label.
sheets = pd.read_excel(FILE_PATH, sheet_name=None)
required_sheets = [
    "Individual Results", "Athlete Lookup", "Meets",
    "Athlete Seasons", "State Standards"
]
require(all(name in sheets for name in required_sheets),
        "The workbook is missing a required sheet, including possibly State Standards.")

results = sheets["Individual Results"].copy()
athletes = sheets["Athlete Lookup"]
meets = sheets["Meets"]
athlete_seasons = sheets["Athlete Seasons"]
standards = sheets["State Standards"].copy()
original_count = len(results)
require(original_count > 0, "No individual results found.")
results["Event"] = results["Event"].replace({"200m (Straight)": "200m"})


# 2. Check source data before joining or calculating.
required_result_columns = ["AthleteID", "MeetID", "Event", "ResultValue", "Unit"]
require(not results[required_result_columns].isna().any().any(),
        "Individual results have missing IDs, events, values, or units.")
results["ResultValue"] = pd.to_numeric(results["ResultValue"], errors="raise")
require(results["ResultValue"].map(math.isfinite).all()
        and results["ResultValue"].gt(0).all(), "Results must be finite positive numbers.")
require(not results.duplicated().any(), "Exact duplicate individual results need review.")

field_event_names = {"Discus", "High Jump", "Long Jump", "Pole Vault", "Shot Put"}
running_event_names = {
    "100m", "200m", "400m", "800m", "1600m", "100m Hurdles", "110m Hurdles"
}
valid_event_units = (
    (results["Event"].isin(field_event_names) & results["Unit"].eq("inches"))
    | (results["Event"].isin(running_event_names) & results["Unit"].eq("seconds"))
)
require(valid_event_units.all(), "An individual event or unit needs review.")

standard_keys = ["Season", "Class", "Gender", "Division", "Event"]
for table, keys, label in [
    (athletes, ["AthleteID"], "Athlete Lookup"),
    (meets, ["MeetID"], "Meets"),
    (athlete_seasons, ["AthleteID", "Season"], "Athlete Seasons"),
    (standards, standard_keys, "State Standards"),
]:
    require(not table[keys].isna().any().any(), f"Missing keys in {label}.")
    require(not table.duplicated(subset=keys).any(), f"Duplicate keys in {label}.")

require(results["AthleteID"].isin(athletes["AthleteID"]).all(), "Unknown athlete IDs.")
require(results["MeetID"].isin(meets["MeetID"]).all(), "Unknown meet IDs.")
require(not standards[["StandardRaw", "StandardValue", "Unit"]].isna().any().any(),
        "Missing standard marks, values, or units. Check Excel formulas and save again.")
standards["StandardValue"] = pd.to_numeric(standards["StandardValue"], errors="raise")
require(standards["StandardValue"].map(math.isfinite).all()
        and standards["StandardValue"].gt(0).all(), "Standards must be finite positive numbers.")

# Catch accidental Excel formula copying rather than silently fixing it.
expected_values = standards.apply(
    lambda row: parse_standard(row["StandardRaw"], row["Unit"]), axis=1
)
bad_standards = (standards["StandardValue"] - expected_values).abs().gt(0.000001)
require(not bad_standards.any(),
        "StandardValue disagrees with StandardRaw. Review these Excel rows: "
        + str((standards.index[bad_standards] + 2).tolist()))


# 3. Join meet, athlete-season, gender, and benchmark information.
meets = meets.copy()
meets["MeetDate"] = pd.to_datetime(meets["MeetDate"], errors="raise").dt.normalize()
results = results.merge(meets[["MeetID", "MeetDate", "Season"]],
                        on="MeetID", how="left", validate="many_to_one")
results = results.merge(
    athlete_seasons[["AthleteID", "Season", "Grade", "SeasonsUnderCoach"]],
    on=["AthleteID", "Season"], how="left", validate="many_to_one"
)
results = results.merge(athletes[["AthleteID", "Gender"]],
                        on="AthleteID", how="left", validate="many_to_one")
require(not results[["MeetDate", "Season", "Grade", "SeasonsUnderCoach", "Gender"]]
        .isna().any().any(), "Missing meet, athlete-season, or gender information after joining.")
results["Division"] = results["Grade"].map({5: 7, 6: 7, 7: 7, 8: 8})
require(results["Division"].notna().all(), "A grade is outside the supported range 5-8.")
results["Class"] = "1A"
match_columns = standard_keys + ["Unit"]
results = results.merge(standards[match_columns + ["StandardValue"]],
                        on=match_columns, how="left", validate="many_to_one")
require(results["StandardValue"].notna().all(), "Some individual results have no matching standard.")
require(len(results) == original_count, "A join changed the number of performances.")


# 4. Calculate season bests, standard attainment, and benchmark margins.
group_columns = ["AthleteID", "Season", "Event", "Unit"]
running_bests = (results.loc[results["Unit"].eq("seconds")]
                 .groupby(group_columns)["ResultValue"].min())
field_bests = (results.loc[results["Unit"].eq("inches")]
               .groupby(group_columns)["ResultValue"].max())
season_bests = (pd.concat([running_bests, field_bests])
                .reset_index(name="SeasonBest")
                .sort_values(["AthleteID", "Season", "Event"]))

# Preserve the existing six-decimal comparison for conversion precision.
performance = results["ResultValue"].round(6)
standard = results["StandardValue"].round(6)
field_events = results["Unit"].eq("inches")
results["MeetsStandard"] = (
    (~field_events & performance.le(standard))
    | (field_events & performance.ge(standard))
)
results["Margin"] = results["StandardValue"] - results["ResultValue"]
results.loc[field_events, "Margin"] *= -1
results["MarginPercent"] = results["Margin"] / results["StandardValue"] * 100

summary = results.groupby("Season").agg(
    TotalPerformances=("MeetsStandard", "size"),
    PerformancesMeetingStandard=("MeetsStandard", "sum"),
    PercentMeetingStandard=("MeetsStandard", "mean")
)
summary["PercentMeetingStandard"] = (summary["PercentMeetingStandard"] * 100).round(2)
meeting_standard = results.loc[results["MeetsStandard"]].copy()
breakdown = (meeting_standard.groupby(["Season", "AthleteID", "Event"])
             .size().reset_index(name="PerformancesMeetingStandard"))
summary["UniqueAthletesMeetingStandard"] = (
    meeting_standard.groupby("Season")["AthleteID"].nunique()
    .reindex(summary.index, fill_value=0)
)
summary["AthleteEventCombinationsMeetingStandard"] = (
    breakdown.groupby("Season").size().reindex(summary.index, fill_value=0)
)

current = results.loc[results["Season"].eq(ANALYSIS_SEASON)]
best_rows = current.groupby(["AthleteID", "Event"])["MarginPercent"].idxmax()
closest = current.loc[best_rows].copy()
closest = closest.loc[~closest["MeetsStandard"]].sort_values("MarginPercent", ascending=False)


# 5. Measure improvement within each season.
improvement_keys = ["AthleteID", "Season", "Event", "Unit"]

# Select the best performance on the first recorded date.
first_results = (
    results.sort_values(
        ["MeetDate", "MarginPercent"],
        ascending=[True, False]
    )
    .drop_duplicates(subset=improvement_keys)
    [improvement_keys + ["MeetDate", "ResultValue"]]
    .rename(columns={
        "MeetDate": "FirstDate",
        "ResultValue": "FirstResult"
    })
)

# Count distinct dates so one-meet entries aren't treated as no improvement.
date_counts = (
    results.groupby(improvement_keys)["MeetDate"]
    .nunique()
    .reset_index(name="DatesRecorded")
)

improvement = (
    first_results
    .merge(season_bests, on=improvement_keys, validate="one_to_one")
    .merge(date_counts, on=improvement_keys, validate="one_to_one")
)

improvement = improvement.loc[
    improvement["DatesRecorded"] >= 2
].copy()

# Positive values mean improvement for both running and field events.
improvement["Improvement"] = (
    improvement["FirstResult"] - improvement["SeasonBest"]
)

is_field = improvement["Unit"] == "inches"
improvement.loc[is_field, "Improvement"] *= -1

# Normalize tiny floating-point differences, including negative zero.
improvement.loc[improvement["Improvement"].round(6).eq(0), "Improvement"] = 0.0

improvement["ImprovementPercent"] = (
    improvement["Improvement"] / improvement["FirstResult"] * 100
)

# Treat tiny conversion differences as zero.
improvement["Improved"] = improvement["Improvement"].round(6) > 0

event_improvement = (
    improvement.groupby(["Season", "Event"])
    .agg(
        AthletesMeasured=("AthleteID", "nunique"),
        AthletesImproved=("Improved", "sum"),
        MedianImprovementPercent=("ImprovementPercent", "median")
    )
)

event_improvement["PercentOfAthletesImproved"] = (
    event_improvement["AthletesImproved"]
    / event_improvement["AthletesMeasured"] * 100
)

# 6. Print concise summaries. Set PRINT_DETAILS = True for athlete-level tables.
print(f"Data checks passed for {len(results)} individual performances.")
print(f"Season bests: {len(season_bests)} athlete-season-event combinations.")
print(f"Within-season comparisons: {len(improvement)} combinations with at least two dates.")
print("\nGrade-based Class 1A benchmark summary (individual events):")
print(summary.to_string())
print("\nWithin-season improvement by event:")
print(event_improvement.round(2).to_string())

if PRINT_DETAILS:
    print("\nAthletes and events meeting standards:")
    print(breakdown.to_string(index=False))
    print("\nSeason bests:")
    print(season_bests.round(2).to_string(index=False))
    print(f"\n{ANALYSIS_SEASON}: closest to a grade-based standard (not yet met):")
    columns = ["AthleteID", "Grade", "Event", "Unit", "ResultValue",
               "StandardValue", "Margin", "MarginPercent"]
    print(closest[columns].head(15).round(2).to_string(index=False))

print("\nBenchmarks are not official state qualifications. Relays are excluded.")
print("Improvement compares the first recorded date with the season best, including that date.")


# 7. Chart helpers keep plotting separate from calculations.
def finish_chart(fig, filename):
    """Save and/or show a chart using the settings at the top."""
    if SAVE_CHARTS:
        fig.savefig(FILE_PATH.parent / filename, dpi=300, bbox_inches="tight")
        print(f"Chart saved: {filename}")
    if SHOW_CHARTS:
        plt.show()
    plt.close(fig)


def style_axes(ax):
    ax.set_axisbelow(True)
    ax.grid(axis="x", color="#E2E8F0", linewidth=0.8)
    ax.tick_params(axis="y", length=0, pad=10)
    for side in ["top", "right", "left"]:
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#CBD5E1")


def chart_event_improvement(season):
    if season not in event_improvement.index.get_level_values("Season"):
        print(f"No eligible improvement data for {season}; chart skipped.")
        return
    data = event_improvement.xs(season, level="Season").sort_values("PercentOfAthletesImproved")
    fig, ax = plt.subplots(figsize=(11, 7))
    ax.barh(data.index, data["PercentOfAthletesImproved"], color="#2563EB")
    for position, (_, row) in enumerate(data.iterrows()):
        label = (f'{row["PercentOfAthletesImproved"]:.1f}%  '
                 f'({int(row["AthletesImproved"])} of {int(row["AthletesMeasured"])})')
        ax.text(1.02, position, label, transform=ax.get_yaxis_transform(), va="center", fontsize=10)
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.set_xlabel("Athletes who beat their first recorded performance")
    ax.set_title(f"{season}: Within-season improvement by event", loc="left", fontsize=15, pad=20)
    style_axes(ax)
    fig.text(0.20, 0.05,
             "Includes athletes with results on at least two different dates in the event.\n"
             "Compares the best mark on the first recorded date with the season best.",
             fontsize=9, color="#555555")
    fig.subplots_adjust(left=0.20, right=0.76, bottom=0.18, top=0.88)
    finish_chart(fig, f"improvement_by_event_{season}.png")


def chart_state_standards(season):
    eligible = meeting_standard.loc[meeting_standard["Season"] == season]
    if eligible.empty:
        print(f"No performances meeting standards in {season}; chart skipped.")
        return
    data = eligible.groupby("Event").agg(
        Performances=("AthleteID", "size"), Athletes=("AthleteID", "nunique")
    ).sort_values("Performances")
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.barh(data.index, data["Performances"], color="#15803D")
    for position, (_, row) in enumerate(data.iterrows()):
        count, athletes_count = int(row["Performances"]), int(row["Athletes"])
        noun = "athlete" if athletes_count == 1 else "athletes"
        ax.text(1.02, position, f"{count} marks, {athletes_count} {noun}",
                transform=ax.get_yaxis_transform(), va="center", fontsize=10)
    ax.set_xlim(0, data["Performances"].max())
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel("Recorded performances meeting or beating the standard")
    fig.suptitle(f"{season}: {len(eligible)} state-standard performances across "
                 f'{eligible["AthleteID"].nunique()} athletes',
                 x=0.20, y=0.96, ha="left", fontsize=14)
    style_axes(ax)
    fig.text(0.20, 0.04,
             f"Individual events only. Compared with {season} Class 1A grade-based standards.\n"
             "Repeated performances count separately; these are not official state qualifications.",
             fontsize=9, color="#555555")
    fig.subplots_adjust(left=0.20, right=0.76, bottom=0.20, top=0.85)
    finish_chart(fig, f"state_standard_performances_{season}.png")


def chart_400m_improvement(season):
    data = improvement.loc[
        (improvement["Season"] == season) & (improvement["Event"] == "400m")
    ].sort_values("SeasonBest").reset_index(drop=True)
    if data.empty:
        print(f"No eligible {season} 400m comparisons; chart skipped.")
        return
    fig, ax = plt.subplots(figsize=(12, max(6, 0.65 * len(data) + 2.5)))
    positions = range(len(data))
    ax.hlines(positions, data["SeasonBest"], data["FirstResult"],
              color="#CBD5E1", linewidth=4, zorder=1)
    ax.scatter(data["FirstResult"], positions, color="#64748B", s=85,
               label="First recorded performance", zorder=3)
    ax.scatter(data["SeasonBest"], positions, color="#15803D", s=85,
               label="Season best", zorder=4)
    for position, row in data.iterrows():
        for column, offset, color in [("FirstResult", 10, "#475569"),
                                       ("SeasonBest", -16, "#15803D")]:
            ax.annotate(f"{row[column]:.2f}", (row[column], position),
                        xytext=(0, offset), textcoords="offset points",
                        ha="center", fontsize=9, color=color)
        label = f'{row["Improvement"]:.2f} s faster' if row["Improved"] else "No improvement"
        ax.text(1.03, position, label, transform=ax.get_yaxis_transform(),
                va="center", fontsize=10, color="#15803D")
    ax.set_yticks(list(positions), labels=data["AthleteID"])
    ax.set_ylim(-0.7, len(data) - 0.3)
    ax.invert_yaxis()
    ax.set_xlim(data["SeasonBest"].min() - 4, data["FirstResult"].max() + 4)
    ax.set_xlabel("400m time in seconds - lower is faster", labelpad=12)
    style_axes(ax)
    improved_count = int(data["Improved"].sum())
    subject = f"All {len(data)}" if improved_count == len(data) else f"{improved_count} of {len(data)}"
    fig.suptitle(f"{subject} measured 400m athletes improved in {season}",
                 x=0.12, y=0.97, ha="left", fontsize=17, fontweight="bold")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), frameon=False, ncol=2, fontsize=10)
    fig.text(0.12, 0.04,
             "Includes athletes with recorded 400m results on at least two dates.\n"
             "Opening time = best mark on the first recorded date. Season best includes that opening performance.",
             fontsize=9, color="#475569")
    fig.subplots_adjust(left=0.12, right=0.80, bottom=0.18, top=0.84)
    finish_chart(fig, f"400m_improvement_{season}.png")


# Turn both settings off to calculate results without creating or opening charts.
if SAVE_CHARTS or SHOW_CHARTS:
    chart_event_improvement(ANALYSIS_SEASON)
    chart_state_standards(HIGHLIGHT_SEASON)
    chart_400m_improvement(HIGHLIGHT_SEASON)

# 8. Compare 400m improvement by seasons under coaching.
import random

# Add coaching experience to each athlete-season-event comparison.
experience = improvement.merge(
    athlete_seasons[["AthleteID", "Season", "SeasonsUnderCoach"]],
    on=["AthleteID", "Season"],
    how="left",
    validate="many_to_one"
)

require(
    experience["SeasonsUnderCoach"].notna().all(),
    "Some improvement records are missing coaching experience."
)

experience_400 = experience.loc[
    (experience["Event"] == "400m") &
    experience["SeasonsUnderCoach"].isin([1, 2, 3])
].copy()

# Also calculate first-to-last change.
# If multiple marks share the final date, use that day's best.
last_results = (
    results.sort_values(
        ["MeetDate", "MarginPercent"],
        ascending=[False, False]
    )
    .drop_duplicates(["AthleteID", "Season", "Event", "Unit"])
    [["AthleteID", "Season", "Event", "Unit", "ResultValue"]]
    .rename(columns={"ResultValue": "LastResult"})
)

experience_400 = experience_400.merge(
    last_results,
    on=["AthleteID", "Season", "Event", "Unit"],
    how="left",
    validate="one_to_one"
)

experience_400["FirstToLastPercent"] = (
    (experience_400["FirstResult"] - experience_400["LastResult"])
    / experience_400["FirstResult"] * 100
)

experience_summary = (
    experience_400.groupby("SeasonsUnderCoach")
    .agg(
        AthleteSeasons=("AthleteID", "size"),
        UniqueAthletes=("AthleteID", "nunique"),
        MedianFirstToBestPct=("ImprovementPercent", "median"),
        MedianFirstToLastPct=("FirstToLastPercent", "median"),
        PercentImproved=("Improved", "mean")
    )
)

experience_summary["PercentImproved"] *= 100

print("\n400m improvement by coaching experience:")
print(experience_summary.round(2).to_string())

# Show how the experience groups are distributed across calendar seasons.
print("\n400m athlete-seasons by year and coaching experience:")
print(pd.crosstab(
    experience_400["Season"],
    experience_400["SeasonsUnderCoach"]
).to_string())

# Draw individual observations and group medians.
if (SAVE_CHARTS or SHOW_CHARTS) and not experience_400.empty:
    fig, ax = plt.subplots(figsize=(10, 7))
    rng = random.Random(42)

    colors = ["#2563EB", "#15803D", "#9333EA"]
    tick_labels = []

    for position, (years, color) in enumerate(
        zip([1, 2, 3], colors), start=1
    ):
        group = experience_400.loc[
            experience_400["SeasonsUnderCoach"] == years,
            "ImprovementPercent"
        ]

        tick_labels.append(
            f"Season {years}\n{len(group)} athlete-seasons"
        )

        if group.empty:
            continue

        # Small horizontal offsets keep overlapping dots visible.
        x_positions = [
            position + rng.uniform(-0.12, 0.12)
            for _ in range(len(group))
        ]

        ax.scatter(
            x_positions, group,
            color=color, alpha=0.7, s=65, zorder=3
        )

        median = group.median()
        ax.hlines(
            median, position - 0.23, position + 0.23,
            color="#0F172A", linewidth=3, zorder=4
        )

        ax.annotate(
            f"Median {median:.2f}%",
            (position + 0.24, median),
            xytext=(4, 0),
            textcoords="offset points",
            fontsize=9,
            va="center"
        )

    ax.set_xticks([1, 2, 3], labels=tick_labels)
    ax.set_xlim(0.5, 3.9)
    ax.set_ylim(bottom=-1)
    ax.margins(y=0.15)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.set_ylabel("Reduction in 400m time: first recorded date to season best")
    ax.set_xlabel("Season under coaching", labelpad=12)
    ax.set_title(
        "400m improvement by coaching experience",
        loc="left", fontsize=16, pad=18
    )

    ax.set_axisbelow(True)
    ax.grid(axis="y", alpha=0.2)

    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)

    fig.text(
        0.12, 0.04,
        "Each dot is one athlete-season with results on at least two dates. "
        "Black lines show medians.\n"
        "All recorded seasons pooled; athletes may appear in multiple groups. "
        "Differences do not isolate coaching effects.",
        fontsize=9, color="#475569"
    )

    fig.subplots_adjust(left=0.12, right=0.97, bottom=0.23, top=0.88)
    finish_chart(fig, "400m_improvement_by_experience.png")

    # 9. Compare coaching experience across all individual events.
# Each athlete receives one observation per season.

all_event_experience = improvement.merge(
    athlete_seasons[["AthleteID", "Season", "SeasonsUnderCoach"]],
    on=["AthleteID", "Season"],
    how="left",
    validate="many_to_one"
)

require(
    all_event_experience["SeasonsUnderCoach"].notna().all(),
    "Missing coaching experience in the combined-event analysis."
)

all_event_experience = all_event_experience.loc[
    all_event_experience["SeasonsUnderCoach"].isin([1, 2, 3])
].copy()

athlete_improvement = (
    all_event_experience
    .groupby(["AthleteID", "Season", "SeasonsUnderCoach"])
    .agg(
        MedianEventImprovementPct=("ImprovementPercent", "median"),
        EventsMeasured=("Event", "nunique")
    )
    .reset_index()
)

combined_summary = (
    athlete_improvement.groupby("SeasonsUnderCoach")
    .agg(
        AthleteSeasons=("AthleteID", "size"),
        UniqueAthletes=("AthleteID", "nunique"),
        EventComparisons=("EventsMeasured", "sum"),
        MedianImprovementPct=("MedianEventImprovementPct", "median")
    )
)

print("\nAll-event improvement by coaching experience:")
print(combined_summary.round(2).to_string())

print("\nAthlete-seasons by calendar year and coaching experience:")
print(pd.crosstab(
    athlete_improvement["Season"],
    athlete_improvement["SeasonsUnderCoach"]
).to_string())

# Show which events contribute to each experience group.
print("\nEvent comparisons in each coaching-experience group:")
print(pd.crosstab(
    all_event_experience["Event"],
    all_event_experience["SeasonsUnderCoach"]
).to_string())

if (SAVE_CHARTS or SHOW_CHARTS) and not athlete_improvement.empty:
    import random

    fig, ax = plt.subplots(figsize=(11, 7))
    rng = random.Random(42)
    colors = ["#2563EB", "#15803D", "#9333EA"]
    labels = []

    for position, (years, color) in enumerate(
        zip([1, 2, 3], colors), start=1
    ):
        group = athlete_improvement.loc[
            athlete_improvement["SeasonsUnderCoach"] == years,
            "MedianEventImprovementPct"
        ]

        labels.append(
            f"Season {years}\n{len(group)} athlete-seasons"
        )

        if group.empty:
            continue

        x_positions = [
            position + rng.uniform(-0.13, 0.13)
            for _ in range(len(group))
        ]

        ax.scatter(
            x_positions, group,
            color=color, alpha=0.65, s=65, zorder=3
        )

        median = group.median()

        ax.hlines(
            median, position - 0.23, position + 0.23,
            color="#0F172A", linewidth=3, zorder=4
        )

        ax.annotate(
            f"Median {median:.2f}%",
            (position + 0.24, median),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=9
        )

    ax.set_xticks([1, 2, 3], labels=labels)
    ax.set_xlim(0.5, 3.9)
    ax.set_ylim(bottom=-1)
    ax.margins(y=0.15)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))

    ax.set_ylabel("Athlete's median first-to-best improvement across events")
    ax.set_xlabel("Season under coaching", labelpad=12)
    ax.set_title(
        "All-event improvement by coaching experience",
        loc="left", fontsize=16, pad=18
    )

    ax.set_axisbelow(True)
    ax.grid(axis="y", alpha=0.2)

    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)

    fig.text(
        0.12, 0.035,
        "One dot per athlete-season; median percentage improvement across eligible individual events.\n"
        "Each event requires results on at least two dates. Black lines show group medians.\n"
        "Event mix, grade, and calendar year may differ; athletes can appear in multiple groups.",
        fontsize=9, color="#475569"
    )

    fig.subplots_adjust(
        left=0.12, right=0.97, bottom=0.25, top=0.88
    )

    finish_chart(fig, "all_event_improvement_by_experience.png")

    # YEAR-TO-YEAR IMPROVEMENT FOR RETURNING ATHLETES

# Calculate season bests directly from the recorded results.
comparison_keys = ["AthleteID", "Season", "Event", "Unit"]

running = (
    results.loc[results["Unit"] == "seconds"]
    .groupby(comparison_keys)["ResultValue"]
    .min()
)

field = (
    results.loc[results["Unit"] == "inches"]
    .groupby(comparison_keys)["ResultValue"]
    .max()
)

annual_bests = pd.concat([running, field]).reset_index(
    name="SeasonBest"
)

# Move the previous season forward one year so matching rows
# represent the same athlete and event in consecutive seasons.
previous = annual_bests.rename(
    columns={"SeasonBest": "PreviousBest"}
).copy()

previous["Season"] += 1

yearly_comparisons = annual_bests.merge(
    previous,
    on=comparison_keys,
    how="inner",
    validate="one_to_one"
)

yearly_comparisons["PreviousSeason"] = (
    yearly_comparisons["Season"] - 1
)

yearly_comparisons["Transition"] = (
    yearly_comparisons["PreviousSeason"].astype(int).astype(str)
    + " → "
    + yearly_comparisons["Season"].astype(int).astype(str)
)

# Lower times and longer/higher distances count as improvement.
yearly_comparisons["Improvement"] = (
    yearly_comparisons["PreviousBest"]
    - yearly_comparisons["SeasonBest"]
)

is_field = yearly_comparisons["Unit"] == "inches"

yearly_comparisons.loc[is_field, "Improvement"] *= -1

# Remove tiny floating-point differences from unit conversions.
yearly_comparisons["Improvement"] = (
    yearly_comparisons["Improvement"].round(6)
)

yearly_comparisons["ImprovementPercent"] = (
    yearly_comparisons["Improvement"]
    / yearly_comparisons["PreviousBest"]
    * 100
)

yearly_comparisons["Improved"] = (
    yearly_comparisons["Improvement"] > 0
)

print("\nYear-to-year comparisons — each athlete and event:")
print(
    yearly_comparisons[
        [
            "Transition", "AthleteID", "Event", "Unit",
            "PreviousBest", "SeasonBest", "ImprovementPercent"
        ]
    ]
    .sort_values(["Transition", "AthleteID", "Event"])
    .round(2)
    .to_string(index=False)
)

# Event-level summary keeps comparisons within the same event.
yearly_event_summary = (
    yearly_comparisons
    .groupby(["Transition", "Event"])
    .agg(
        AthletesCompared=("AthleteID", "nunique"),
        AthletesImproved=("Improved", "sum"),
        MedianImprovementPercent=("ImprovementPercent", "median")
    )
)

yearly_event_summary["PercentImproved"] = (
    yearly_event_summary["AthletesImproved"]
    / yearly_event_summary["AthletesCompared"]
    * 100
)

print("\nYear-to-year improvement by event:")
print(yearly_event_summary.round(2).to_string())

# Combine events with one summary per athlete per transition.
# This gives each athlete equal weight, regardless of event count.
athlete_yearly = (
    yearly_comparisons
    .groupby(["AthleteID", "Transition"])
    .agg(
        EventsCompared=("Event", "nunique"),
        MedianEventImprovementPercent=("ImprovementPercent", "median")
    )
    .reset_index()
)

athlete_yearly["PositiveMedian"] = (
    athlete_yearly["MedianEventImprovementPercent"] > 0
)

yearly_summary = (
    athlete_yearly
    .groupby("Transition")
    .agg(
        AthletesCompared=("AthleteID", "nunique"),
        EventComparisons=("EventsCompared", "sum"),
        AthletesWithPositiveMedian=("PositiveMedian", "sum"),
        MedianAthleteImprovementPercent=(
            "MedianEventImprovementPercent", "median"
        )
    )
)

print("\nAll-event summary — each athlete weighted equally:")
print(yearly_summary.round(2).to_string())

import random
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

output_folder = Path(__file__).parent
transitions = sorted(athlete_yearly["Transition"].unique())
colors = ["#2563eb", "#15803d", "#9333ea"]

# --------------------------------------------------
# GRAPH 1: ONE DOT PER ATHLETE PER YEARLY TRANSITION
# --------------------------------------------------

fig, ax = plt.subplots(figsize=(11, 7))
rng = random.Random(42)
labels = []

for position, transition in enumerate(transitions):
    values = athlete_yearly.loc[
        athlete_yearly["Transition"] == transition,
        "MedianEventImprovementPercent"
    ]

    median = values.median()
    positive = (values > 0).sum()
    color = colors[position % len(colors)]

    jitter = [
        position + rng.uniform(-0.13, 0.13)
        for _ in range(len(values))
    ]

    ax.scatter(
        jitter, values,
        s=90, color=color, alpha=0.75,
        edgecolors="white", linewidths=0.8,
        zorder=3
    )

    ax.hlines(
        median, position - 0.23, position + 0.23,
        color="#0f172a", linewidth=4, zorder=4
    )

    ax.annotate(
        f"Median {median:.2f}%",
        xy=(position + 0.24, median),
        xytext=(5, 5),
        textcoords="offset points",
        fontsize=11
    )

    labels.append(
        f"{transition}\n"
        f"{len(values)} athletes; {positive} with positive medians"
    )

ax.axhline(0, color="#64748b", linestyle="--", linewidth=1.2)
ax.set_xticks(range(len(transitions)))
ax.set_xticklabels(labels, fontsize=11)
ax.set_xlim(-0.5, len(transitions) - 0.3)
ax.margins(y=0.15)

ax.set_title(
    "Returning athletes: year-to-year improvement",
    fontsize=19, loc="left", pad=20
)
ax.set_ylabel(
    "Athlete’s median improvement across matched events",
    fontsize=12
)
ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
ax.grid(axis="y", alpha=0.2)
ax.set_axisbelow(True)
ax.spines[["top", "right"]].set_visible(False)

fig.subplots_adjust(bottom=0.25, left=0.12, right=0.96)

fig.text(
    0.12, 0.04,
    "Each dot represents one athlete in one yearly transition; "
    "black lines show group medians.\n"
    "Only events recorded in both years are compared. "
    "Positive = improvement; negative = decline.\n"
    "Athletes may appear in both transitions. "
    "Changes do not isolate coaching effects.",
    fontsize=10, color="#555555"
)

path = output_folder / "returning_athlete_improvement.png"
fig.savefig(path, dpi=200, bbox_inches="tight")
print(f"\nSaved: {path}")
plt.close(fig)


# --------------------------------------------------
# GRAPH 2: ONE DOT PER EVENT PER YEARLY TRANSITION
# --------------------------------------------------

event_plot = yearly_event_summary.reset_index()

# Use the same alphabetical event order in every panel.
events = sorted(event_plot["Event"].unique())
event_positions = {
    event: position for position, event in enumerate(events)
}

fig, axes = plt.subplots(
    1, len(transitions),
    figsize=(14, 8),
    sharex=True,
    sharey=True,
    squeeze=False
)

for position, transition in enumerate(transitions):
    ax = axes[0, position]
    color = colors[position % len(colors)]

    subset = event_plot.loc[
        event_plot["Transition"] == transition
    ]

    for _, row in subset.iterrows():
        y = event_positions[row["Event"]]
        value = row["MedianImprovementPercent"]
        count = int(row["AthletesCompared"])

        ax.hlines(
            y, min(0, value), max(0, value),
            color=color, alpha=0.35, linewidth=3
        )

        ax.scatter(
            value, y,
            s=100, color=color,
            edgecolors="white", linewidths=0.8,
            zorder=3
        )

        ax.annotate(
            f"{value:.2f}%  (n={count})",
            xy=(value, y),
            xytext=(9, 0),
            textcoords="offset points",
            va="center", fontsize=10
        )

    ax.axvline(
        0, color="#64748b",
        linestyle="--", linewidth=1.2
    )
    ax.set_title(transition, fontsize=16, pad=15)
    ax.set_xlabel("Median season-best improvement", fontsize=11)
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.grid(axis="x", alpha=0.2)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)

axes[0, 0].set_yticks(range(len(events)))
axes[0, 0].set_yticklabels(events, fontsize=11)
axes[0, 0].invert_yaxis()

# Shared scale, with room for labels beside the dots.
minimum = min(0, event_plot["MedianImprovementPercent"].min())
maximum = max(0, event_plot["MedianImprovementPercent"].max())
span = max(maximum - minimum, 5)

axes[0, 0].set_xlim(
    minimum - span * 0.08,
    maximum + span * 0.50
)

fig.suptitle(
    "Returning athletes: year-to-year improvement by event",
    fontsize=19, y=0.97
)

fig.subplots_adjust(
    left=0.13, right=0.97,
    bottom=0.23, top=0.87, wspace=0.12
)

fig.text(
    0.13, 0.04,
    "Each dot is the median percentage change among athletes "
    "with that event recorded in both years; n = athletes compared.\n"
    "Blank rows mean no matched athletes. Positive = improvement; "
    "negative = decline. Samples are small (1–5 athletes per event).\n"
    "Percentage gains have different meanings across events. "
    "Age, competition opportunities, and other factors also contribute.",
    fontsize=10, color="#555555"
)

path = output_folder / "yearly_improvement_by_event.png"
fig.savefig(path, dpi=200, bbox_inches="tight")
print(f"Saved: {path}")
plt.close(fig)

# ROSTER GROWTH, RETURNING ATHLETES, AND PRIOR-YEAR GRADUATES

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

seasons = sorted(results["Season"].dropna().astype(int).unique())

roster = athlete_seasons[
    ["AthleteID", "Season", "Grade"]
].copy()

if roster.duplicated(["AthleteID", "Season"]).any():
    raise ValueError("Duplicate athlete-season entries in Athlete Seasons.")

if roster[["AthleteID", "Season", "Grade"]].isna().any().any():
    raise ValueError("Missing athlete ID, season, or grade in Athlete Seasons.")

roster["Season"] = pd.to_numeric(roster["Season"], errors="raise")
roster["Grade"] = pd.to_numeric(roster["Grade"], errors="raise")

rows = []

for season in seasons:
    current_ids = set(
        roster.loc[roster["Season"] == season, "AthleteID"]
    )

    has_previous = (season - 1) in seasons

    previous = roster.loc[roster["Season"] == season - 1]
    previous_ids = set(previous["AthleteID"])

    graduates = set(
        previous.loc[previous["Grade"] == 8, "AthleteID"]
    )

    # Flag unexpected records instead of silently misclassifying them.
    if has_previous and graduates & current_ids:
        raise ValueError(
            f"Prior-year eighth graders appear on the {season} roster: "
            f"{sorted(graduates & current_ids)}. Check their grades/seasons."
        )

    rows.append({
        "Season": season,
        "RosterSize": len(current_ids),
        "Returning": (
            len(current_ids & previous_ids) if has_previous else pd.NA
        ),
        "NotOnPreviousRoster": (
            len(current_ids - previous_ids) if has_previous else pd.NA
        ),
        "PreviousSeasonGraduates": (
            len(graduates) if has_previous else pd.NA
        )
    })

roster_summary = pd.DataFrame(rows)

print("\nRoster growth and previous-season graduates:")
print(roster_summary.to_string(index=False))

# Roster above zero; previous-season graduates below zero.

fig, ax = plt.subplots(figsize=(11, 8))
used_labels = set()

for position, row in roster_summary.iterrows():
    season = int(row["Season"])
    total = int(row["RosterSize"])

    if pd.isna(row["Returning"]):
        segments = [
            ("Starting roster", total, "#94a3b8", "")
        ]
    else:
        returning = int(row["Returning"])
        other = int(row["NotOnPreviousRoster"])

        segments = [
            (
                "Returning athletes",
                returning,
                "#15803d",
                f"{returning} returning"
            ),
            (
                "Not on previous season's roster",
                other,
                "#2563eb",
                f"{other} not on\nprevious roster"
            )
        ]

    bottom = 0

    for label, count, color, text in segments:
        ax.bar(
            position, count,
            bottom=bottom,
            width=0.6,
            color=color,
            label=label if label not in used_labels else None
        )
        used_labels.add(label)

        if count > 0 and text:
            ax.text(
                position, bottom + count / 2,
                text,
                ha="center", va="center",
                color="white", fontsize=11
            )

        bottom += count

    ax.text(
        position, total + 0.7,
        f"{total} athletes",
        ha="center", fontsize=13, fontweight="bold"
    )

    # Graduates are displayed below zero, beneath the following year.
    if pd.notna(row["PreviousSeasonGraduates"]):
        graduates = int(row["PreviousSeasonGraduates"])
        label = "Graduated after previous season"

        ax.bar(
            position, -graduates,
            width=0.6,
            color="#c2410c",
            label=label if label not in used_labels else None
        )
        used_labels.add(label)

        ax.text(
            position, -graduates - 0.7,
            f"{graduates} graduated",
            ha="center", va="top",
            color="#9a3412", fontsize=11
        )

max_roster = int(roster_summary["RosterSize"].max())
graduate_counts = roster_summary["PreviousSeasonGraduates"].dropna()
max_graduates = (
    int(graduate_counts.max()) if len(graduate_counts) else 0
)

ax.set_ylim(
    -max_graduates - 5,
    max_roster * 1.18
)

ax.axhline(0, color="#0f172a", linewidth=1.5)

ax.set_xticks(range(len(roster_summary)))
ax.set_xticklabels(
    roster_summary["Season"].astype(int).astype(str),
    fontsize=12
)

ax.set_title(
    "Team growth and graduation departures",
    fontsize=18, loc="left", pad=20
)
ax.set_ylabel(
    "Athletes: roster above zero; prior-season graduates below",
    fontsize=11
)
ax.yaxis.set_major_locator(MaxNLocator(integer=True))

ax.grid(axis="y", alpha=0.2)
ax.set_axisbelow(True)
ax.spines[["top", "right"]].set_visible(False)

fig.subplots_adjust(
    bottom=0.31, left=0.11, right=0.97
)

fig.legend(
    *ax.get_legend_handles_labels(),
    loc="lower left",
    bbox_to_anchor=(0.11, 0.10),
    frameon=False,
    fontsize=10,
    ncol=2
)

fig.text(
    0.11, 0.025,
    "Above zero: current season's listed roster. "
    "Below zero: grade-8 athletes from the previous season's roster.\n"
    "Example: the graduation bar under 2025 counts 2024 eighth graders. "
    "No graduation comparison is shown for the baseline year.\n"
    "Graduates are not included in the current roster totals. "
    "Younger athletes who did not return are not shown as departures.",
    fontsize=9, color="#555555"
)

output_path = Path(__file__).parent / "roster_and_returning_athletes.png"
fig.savefig(output_path, dpi=200, bbox_inches="tight")
plt.close(fig)

print(f"\nSaved: {output_path}")

# 2026 EVENT DEVELOPMENT: FREQUENCY AND SIZE OF IMPROVEMENT

import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

season_to_plot = 2026

event_data = (
    event_improvement
    .xs(season_to_plot, level="Season")
    .reset_index()
    .sort_values(
        ["PercentOfAthletesImproved", "AthletesMeasured"],
        ascending=[False, False]
    )
    .reset_index(drop=True)
)

# Clean up tiny floating-point values that display as -0.00.
event_data["MedianImprovementPercent"] = (
    event_data["MedianImprovementPercent"].round(6) + 0.0
)

positions = list(range(len(event_data)))

fig, (ax_share, ax_amount) = plt.subplots(
    1, 2,
    figsize=(15, 8),
    sharey=True,
    gridspec_kw={"width_ratios": [1.2, 1]}
)

# LEFT: Percentage of athletes who exceeded their opening mark.
ax_share.barh(
    positions,
    event_data["PercentOfAthletesImproved"],
    color="#2563eb",
    height=0.65
)

for position, row in event_data.iterrows():
    improved = int(row["AthletesImproved"])
    measured = int(row["AthletesMeasured"])
    share = row["PercentOfAthletesImproved"]

    ax_share.text(
        share + 2,
        position,
        f"{share:.1f}% ({improved}/{measured})",
        va="center",
        fontsize=10
    )

ax_share.set_yticks(positions)
ax_share.set_yticklabels(event_data["Event"], fontsize=11)
ax_share.invert_yaxis()

ax_share.set_xlim(0, 133)
ax_share.set_xticks([0, 25, 50, 75, 100])
ax_share.xaxis.set_major_formatter(PercentFormatter(xmax=100))
ax_share.set_title("How many improved?", fontsize=15, pad=15)
ax_share.set_xlabel("Share who beat their opening mark", fontsize=11)

# RIGHT: Median improvement, including athletes with no improvement.
ax_amount.hlines(
    positions,
    0,
    event_data["MedianImprovementPercent"],
    color="#15803d",
    linewidth=3,
    alpha=0.4
)

ax_amount.scatter(
    event_data["MedianImprovementPercent"],
    positions,
    color="#15803d",
    s=85,
    zorder=3
)

largest_median = max(
    float(event_data["MedianImprovementPercent"].max()), 1.0
)
label_offset = largest_median * 0.035

for position, row in event_data.iterrows():
    median = row["MedianImprovementPercent"]

    ax_amount.text(
        median + label_offset,
        position,
        f"{median:.2f}%",
        va="center",
        fontsize=10
    )

ax_amount.set_xlim(
    -largest_median * 0.04,
    largest_median * 1.28
)
ax_amount.xaxis.set_major_formatter(PercentFormatter(xmax=100))
ax_amount.set_title("How much did they improve?", fontsize=15, pad=15)
ax_amount.set_xlabel("Median improvement from opening mark", fontsize=11)
ax_amount.tick_params(axis="y", left=False)

for ax in (ax_share, ax_amount):
    ax.grid(axis="x", alpha=0.18)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)

fig.suptitle(
    f"{season_to_plot}: Within-season development by event",
    fontsize=21,
    y=0.97
)

fig.subplots_adjust(
    left=0.12,
    right=0.97,
    top=0.86,
    bottom=0.23,
    wspace=0.18
)

fig.text(
    0.12, 0.045,
    "Includes athletes with results on at least two different dates "
    "in the event. Opening mark = best result on the first recorded date.\n"
    "Improvement compares that opening mark with the season best. "
    "Medians include athletes with zero improvement.\n"
    "Positive percentages mean faster times or greater distances/heights. "
    "Percentage gains are not equally difficult across events.\n"
    "Season best includes the opening mark, so this measure cannot show "
    "decline. Athletes may appear in multiple events.",
    fontsize=9.5,
    color="#555555"
)

output_path = (
    Path(__file__).parent / f"event_development_{season_to_plot}.png"
)

fig.savefig(output_path, dpi=200, bbox_inches="tight")
plt.close(fig)

print(f"\nSaved: {output_path}")

# INDIVIDUAL IMPROVEMENT BY EVENT — 2026

import random
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

plot_season = 2026

development = improvement.loc[
    improvement["Season"] == plot_season
].copy()

# Remove tiny conversion differences that could display as -0.00.
development["ImprovementPercent"] = (
    development["ImprovementPercent"].round(6) + 0.0
)

# Match the order of the previous two-panel chart.
event_order = (
    event_improvement
    .xs(plot_season, level="Season")
    .reset_index()
    .sort_values(
        ["PercentOfAthletesImproved", "AthletesMeasured"],
        ascending=[False, False]
    )["Event"]
    .tolist()
)

fig, ax = plt.subplots(figsize=(12, 8))
rng = random.Random(42)
event_labels = []

for position, event in enumerate(event_order):
    values = development.loc[
        development["Event"] == event,
        "ImprovementPercent"
    ]

    median = values.median()

    # Vertical jitter separates overlapping dots.
    dot_positions = [
        position + rng.uniform(-0.17, 0.17)
        for _ in range(len(values))
    ]

    ax.scatter(
        values,
        dot_positions,
        s=65,
        color="#2563eb",
        alpha=0.65,
        edgecolors="white",
        linewidths=0.6,
        zorder=3,
        label="One athlete in one event" if position == 0 else None
    )

    ax.vlines(
        median,
        position - 0.28,
        position + 0.28,
        color="#0f172a",
        linewidth=3,
        zorder=4,
        label="Event median" if position == 0 else None
    )

    # Keep median labels aligned in a separate column.
    ax.text(
        1.02,
        position,
        f"{median:.2f}%",
        transform=ax.get_yaxis_transform(),
        va="center",
        fontsize=11
    )

    event_labels.append(f"{event}  (n={len(values)})")

ax.text(
    1.02, 1.02,
    "Median",
    transform=ax.transAxes,
    fontsize=11,
    fontweight="bold"
)

ax.set_yticks(range(len(event_order)))
ax.set_yticklabels(event_labels, fontsize=11)
ax.invert_yaxis()

largest_gain = max(float(development["ImprovementPercent"].max()), 1)

ax.set_xlim(-largest_gain * 0.04, largest_gain * 1.08)
ax.xaxis.set_major_formatter(PercentFormatter(xmax=100))
ax.axvline(0, color="#64748b", linestyle="--", linewidth=1)

ax.set_title(
    f"{plot_season}: How improvement varied within each event",
    fontsize=18,
    loc="left",
    pad=30
)

ax.set_xlabel(
    "Improvement from first recorded date to season best",
    fontsize=12
)

ax.grid(axis="x", alpha=0.2)
ax.set_axisbelow(True)
ax.spines[["top", "right", "left"]].set_visible(False)
ax.tick_params(axis="y", length=0)

fig.subplots_adjust(
    left=0.20,
    right=0.86,
    top=0.86,
    bottom=0.23
)

fig.legend(
    *ax.get_legend_handles_labels(),
    loc="lower left",
    bbox_to_anchor=(0.20, 0.10),
    frameon=False,
    ncol=2
)

fig.text(
    0.20, 0.025,
    "Includes athletes with results on at least two dates per event. "
    "Each athlete may appear in multiple events.\n"
    "Opening mark = best result on the first recorded date. "
    "Season best includes that mark, so improvement cannot be negative.\n"
    "Dots are shifted vertically for visibility; only horizontal position "
    "represents improvement. Percentage gains differ in meaning across events.",
    fontsize=9,
    color="#555555"
)

output_path = (
    Path(__file__).parent / f"event_improvement_spread_{plot_season}.png"
)

fig.savefig(output_path, dpi=200, bbox_inches="tight")
plt.close(fig)

print(f"\nSaved: {output_path}")


# ============================================================
# SCATTER PLOT ANALYSIS: RELATIONSHIPS BETWEEN SPRINT EVENTS
# ============================================================

import numpy as np


def chart_sprint_correlation(event_x, event_y):
    """
    Compare season-best times for two events.
    Each point represents one athlete in one season.
    """

    # Only use season bests from the selected events.
    sprint_data = season_bests.loc[
        season_bests["Event"].isin([event_x, event_y])
    ].copy()

    # Put the two events in separate columns.
    paired = sprint_data.pivot_table(
        index=["AthleteID", "Season"],
        columns="Event",
        values="SeasonBest",
        aggfunc="first"
    ).dropna(subset=[event_x, event_y])

    x = paired[event_x].to_numpy(dtype=float)
    y = paired[event_y].to_numpy(dtype=float)
    n = len(paired)

    if n < 3 or len(np.unique(x)) < 2 or len(np.unique(y)) < 2:
        print(f"Not enough variation for {event_x} vs {event_y}.")
        return

    # Pearson correlation coefficient.
    r = np.corrcoef(x, y)[0, 1]

    # Linear regression line.
    slope, intercept = np.polyfit(x, y, 1)
    x_line = np.linspace(x.min(), x.max(), 100)
    y_line = slope * x_line + intercept

    # Create the figure.
    fig, ax = plt.subplots(figsize=(10, 7))

    ax.scatter(
        x, y,
        color="#2563EB",
        s=85,
        alpha=0.75,
        edgecolors="white",
        linewidth=0.8,
        label="Athlete-season"
    )

    ax.plot(
        x_line, y_line,
        color="#0F172A",
        linestyle="--",
        linewidth=2,
        label="Linear best-fit line"
    )

    ax.set_title(
        f"How closely are {event_x} and {event_y} times related?",
        fontsize=17,
        loc="left",
        pad=20
    )

    ax.set_xlabel(f"{event_x} season-best time (seconds)")
    ax.set_ylabel(f"{event_y} season-best time (seconds)")

    # Correlation and sample size.
    ax.text(
        0.04, 0.96,
        f"Pearson r = {r:.3f}\nn = {n} athlete-seasons",
        transform=ax.transAxes,
        va="top",
        fontsize=12,
        bbox=dict(
            facecolor="white",
            edgecolor="#CBD5E1",
            boxstyle="round,pad=0.6"
        )
    )

    ax.grid(alpha=0.2)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="lower right", frameon=False)

    fig.subplots_adjust(bottom=0.21, top=0.87)

    fig.text(
        0.12, 0.045,
        "Holy Cross Track & Field, 2024-2026. "
        "One point per athlete-season with both events recorded.\n"
        "Lower times are faster. Athletes may appear in multiple seasons.\n"
        "Correlation does not establish causation.",
        fontsize=9,
        color="#475569"
    )

    filename = f"correlation_{event_x}_vs_{event_y}.png"

    finish_chart(fig, filename)

    print(
        f"{event_x} vs {event_y}: "
        f"r = {r:.3f}, n = {n} athlete-seasons"
    )


# Generate both scatter plots.
chart_sprint_correlation("100m", "200m")
chart_sprint_correlation("200m", "400m")


# ============================================================
# SCATTER PLOTS: ATHLETE IMPROVEMENT RELATIONSHIPS
# ============================================================

def plot_improvement_relationship(data, x_col, y_col,
                                  title, x_label, y_label,
                                  filename, footnote):
    """Create a scatter plot with Pearson r and a best-fit line."""

    data = data[[x_col, y_col]].dropna().copy()
    n = len(data)

    if n < 3:
        print(f"{title}: Only {n} observations. Chart skipped.")
        return

    x = data[x_col].to_numpy(dtype=float)
    y = data[y_col].to_numpy(dtype=float)

    if np.std(x) < 1e-10 or np.std(y) < 1e-10:
        print(f"{title}: Insufficient variation for correlation.")
        return

    r = np.corrcoef(x, y)[0, 1]
    slope, intercept = np.polyfit(x, y, 1)

    x_line = np.linspace(x.min(), x.max(), 100)

    fig, ax = plt.subplots(figsize=(10, 7))

    ax.scatter(
        x, y,
        color="#2563EB",
        s=85,
        alpha=0.75,
        edgecolors="white",
        linewidth=0.8,
        label="Athlete"
    )

    ax.plot(
        x_line,
        slope * x_line + intercept,
        color="#0F172A",
        linestyle="--",
        linewidth=2,
        label="Linear best-fit line"
    )

    ax.text(
        0.04, 0.96,
        f"Pearson r = {r:.3f}\nn = {n}",
        transform=ax.transAxes,
        va="top",
        fontsize=12,
        bbox=dict(
            facecolor="white",
            edgecolor="#CBD5E1",
            boxstyle="round,pad=0.6"
        )
    )

    ax.set_title(title, fontsize=17, loc="left", pad=20)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)

    ax.grid(alpha=0.2)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="lower right", frameon=False)

    fig.subplots_adjust(bottom=0.23, top=0.87)

    fig.text(
        0.12, 0.035,
        footnote,
        fontsize=9,
        color="#475569"
    )

    finish_chart(fig, filename)

    print(f"{title}: r = {r:.3f}, n = {n}")


# ------------------------------------------------------------
# GRAPH 3: OPENING 400m TIME VS WITHIN-SEASON IMPROVEMENT
# ------------------------------------------------------------

opening_400 = improvement.loc[
    (improvement["Season"] == 2026) &
    (improvement["Event"] == "400m")
].copy()

plot_improvement_relationship(
    data=opening_400,
    x_col="FirstResult",
    y_col="Improvement",
    title="Do slower starting 400m athletes improve more?",
    x_label="First recorded 400m time (seconds)",
    y_label="Improvement to season best (seconds)",
    filename="correlation_400m_opening_vs_improvement.png",
    footnote=(
        "Holy Cross, 2026. One dot per athlete with results "
        "on at least two dates.\n"
        "Positive improvement = faster season-best time. "
        "Season best includes the opening mark.\n"
        "Opening time is also part of the improvement "
        "calculation; correlation is descriptive."
    )
)


# ------------------------------------------------------------
# GRAPH 4: DOES YEAR-TO-YEAR IMPROVEMENT PERSIST?
# ------------------------------------------------------------

# Use the existing year-to-year season-best comparisons.
# Keep only the two consecutive transitions.

persistence = yearly_comparisons.loc[
    yearly_comparisons["Transition"].isin([
        "2024 → 2025",
        "2025 → 2026"
    ])
].copy()

# Match the same athlete AND event across both transitions.
# This requires a valid season best in all three years.
paired_improvement = persistence.pivot(
    index=["AthleteID", "Event"],
    columns="Transition",
    values="ImprovementPercent"
).dropna(subset=["2024 → 2025", "2025 → 2026"])

# Give each athlete one observation by taking the median
# improvement across events present in all three seasons.
persistence_by_athlete = (
    paired_improvement
    .groupby(level="AthleteID")
    .median()
    .reset_index()
)

persistence_by_athlete = persistence_by_athlete.rename(
    columns={
        "2024 → 2025": "ImprovementYear1",
        "2025 → 2026": "ImprovementYear2"
    }
)

print(
    "\nAthletes with the same event recorded "
    "in all three seasons:",
    len(persistence_by_athlete)
)

plot_improvement_relationship(
    data=persistence_by_athlete,
    x_col="ImprovementYear1",
    y_col="ImprovementYear2",
    title="Does year-to-year improvement persist?",
    x_label="2024 to 2025 improvement (%)",
    y_label="2025 to 2026 improvement (%)",
    filename="correlation_improvement_persistence.png",
    footnote=(
        "Holy Cross, 2024-2026. One dot per athlete.\n"
        "Only events recorded in all three seasons are used; "
        "athlete values are medians across matched events.\n"
        "Positive = faster times or better field marks. "
        "Negative = decline. Small samples limit conclusions."
    )
)


# ============================================================
# COMBINED SPRINT CORRELATIONS + GENDER ANALYSIS
# ============================================================

import numpy as np

# Pair season-best performances for each athlete and season.
sprint_events = ["100m", "200m", "400m"]

sprint_pairs = (
    season_bests.loc[season_bests["Event"].isin(sprint_events)]
    .pivot(
        index=["AthleteID", "Season"],
        columns="Event",
        values="SeasonBest"
    )
    .reset_index()
)

# Add gender using the original athlete lookup.
sprint_pairs = sprint_pairs.merge(
    athletes[["AthleteID", "Gender"]],
    on="AthleteID",
    how="left",
    validate="many_to_one"
)

require(
    sprint_pairs["Gender"].notna().all(),
    "Missing gender in sprint correlation data."
)

# Create a consistent color for each gender.
gender_labels = sorted(sprint_pairs["Gender"].unique())
palette = ["#2563EB", "#E879A8", "#15803D", "#9333EA"]
gender_colors = {
    gender: palette[i % len(palette)]
    for i, gender in enumerate(gender_labels)
}


def pearson_value(data, x_event, y_event):
    """Return Pearson r when there is sufficient variation."""
    if len(data) < 3:
        return np.nan

    x = data[x_event].to_numpy(dtype=float)
    y = data[y_event].to_numpy(dtype=float)

    if np.ptp(x) == 0 or np.ptp(y) == 0:
        return np.nan

    return np.corrcoef(x, y)[0, 1]


fig, axes = plt.subplots(1, 2, figsize=(16, 7.5))

comparisons = [
    ("100m", "200m"),
    ("200m", "400m")
]

for ax, (x_event, y_event) in zip(axes, comparisons):

    paired = sprint_pairs.dropna(
        subset=[x_event, y_event]
    ).copy()

    n = len(paired)
    r = pearson_value(paired, x_event, y_event)

    # Plot each gender in a different color.
    for gender in gender_labels:
        group = paired.loc[paired["Gender"] == gender]

        ax.scatter(
            group[x_event],
            group[y_event],
            color=gender_colors[gender],
            s=75,
            alpha=0.75,
            edgecolors="white",
            linewidth=0.7,
            label=f"{gender} (n={len(group)})"
        )

    # Overall regression line.
    if np.isfinite(r):
        x = paired[x_event].to_numpy(dtype=float)
        y = paired[y_event].to_numpy(dtype=float)

        slope, intercept = np.polyfit(x, y, 1)
        x_line = np.linspace(x.min(), x.max(), 100)

        ax.plot(
            x_line,
            slope * x_line + intercept,
            color="#0F172A",
            linestyle="--",
            linewidth=2,
            label="Overall best-fit line"
        )

    # Calculate correlations separately by gender.
    print(f"\n{x_event} vs {y_event}")
    print(f"Overall: n={n}, r={r:.3f}")

    gender_results = []

    for gender in gender_labels:
        group = paired.loc[paired["Gender"] == gender]
        group_r = pearson_value(group, x_event, y_event)

        if np.isfinite(group_r):
            result = f"{gender}: r={group_r:.3f} (n={len(group)})"
        else:
            result = f"{gender}: insufficient data (n={len(group)})"

        gender_results.append(result)
        print(result)

    # Add statistical results inside each plot.
    stats_text = (
        f"Overall r = {r:.3f}\n"
        f"n = {n}\n\n"
        + "\n".join(gender_results)
    )

    ax.text(
        0.04, 0.96,
        stats_text,
        transform=ax.transAxes,
        va="top",
        fontsize=10,
        bbox=dict(
            facecolor="white",
            edgecolor="#CBD5E1",
            boxstyle="round,pad=0.6"
        )
    )

    ax.set_title(
        f"{x_event} vs. {y_event}",
        fontsize=16,
        pad=15
    )

    ax.set_xlabel(f"{x_event} season best (seconds)")
    ax.set_ylabel(f"{y_event} season best (seconds)")

    ax.grid(alpha=0.2)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)

    ax.legend(
        loc="lower right",
        fontsize=9,
        frameon=False
    )

fig.suptitle(
    "How strongly are sprint performances related?",
    fontsize=21,
    y=0.98
)

fig.subplots_adjust(
    left=0.07,
    right=0.97,
    bottom=0.21,
    top=0.86,
    wspace=0.25
)

fig.text(
    0.07, 0.045,
    "Holy Cross Track & Field, 2024-2026. "
    "Each dot represents one athlete-season with both events recorded.\n"
    "Colors indicate gender. Dashed lines show pooled linear regression.\n"
    "Athletes may appear in multiple seasons. "
    "Differences may reflect age, event specialization, and other factors.",
    fontsize=10,
    color="#475569"
)

finish_chart(fig, "combined_sprint_correlations.png")


# ------------------------------------------------------------
# ADDITIONAL CHECK: SAME ATHLETES ACROSS ALL THREE EVENTS
# ------------------------------------------------------------

# Compare both correlations on an identical sample.
common_sample = sprint_pairs.dropna(
    subset=["100m", "200m", "400m"]
).copy()

print("\nCOMMON-SAMPLE CORRELATION CHECK")
print("Athlete-seasons with all three events:", len(common_sample))

for x_event, y_event in comparisons:
    r = pearson_value(common_sample, x_event, y_event)
    print(f"{x_event} vs {y_event}: r={r:.3f}")


# ============================================================
# GRAPH: ATHLETE DEVELOPMENT THROUGHOUT THE SEASON
# ============================================================

# Focus on 2026, our most complete season.
season_to_analyze = 2026

timeline = results.loc[
    results["Season"] == season_to_analyze
].copy()

# Use one athlete, event, and date combination.
# If multiple results exist on the same date, keep the best.
timeline = timeline.sort_values(
    "ResultValue",
    ascending=True
)

running = timeline["Unit"] == "seconds"
field = timeline["Unit"] == "inches"

daily_running = (
    timeline.loc[running]
    .sort_values("ResultValue", ascending=True)
    .drop_duplicates(["AthleteID", "Event", "MeetDate"])
)

daily_field = (
    timeline.loc[field]
    .sort_values("ResultValue", ascending=False)
    .drop_duplicates(["AthleteID", "Event", "MeetDate"])
)

timeline = pd.concat(
    [daily_running, daily_field],
    ignore_index=True
)

# First recorded performance in each event.
timeline = timeline.sort_values("MeetDate")

keys = ["AthleteID", "Event"]

timeline["FirstDate"] = (
    timeline.groupby(keys)["MeetDate"].transform("min")
)

timeline["FirstResult"] = (
    timeline.groupby(keys)["ResultValue"].transform("first")
)

# Count unique competition dates.
timeline["Appearances"] = (
    timeline.groupby(keys)["MeetDate"].transform("nunique")
)

# Require at least two dates for an improvement comparison.
timeline = timeline.loc[
    timeline["Appearances"] >= 2
].copy()

# Measure weeks since the first recorded event performance.
timeline["Week"] = (
    (timeline["MeetDate"] - timeline["FirstDate"]).dt.days // 7
).astype(int)

# Positive percentage means a better performance.
timeline["ChangePercent"] = (
    (timeline["FirstResult"] - timeline["ResultValue"])
    / timeline["FirstResult"] * 100
)

field_mask = timeline["Unit"] == "inches"

timeline.loc[field_mask, "ChangePercent"] *= -1

# Give each athlete-event one observation per week.
# Multiple marks in the same week are summarized by their median.
weekly_athletes = (
    timeline.groupby(
        ["AthleteID", "Event", "Week"]
    )["ChangePercent"]
    .median()
    .reset_index()
)

# Calculate the median change for each week.
weekly_summary = (
    weekly_athletes.groupby("Week")
    .agg(
        MedianChange=("ChangePercent", "median"),
        Observations=("ChangePercent", "size")
    )
    .reset_index()
    .sort_values("Week")
)

# Remove Week 5 because the sample is too small to be reliable.
weekly_summary = weekly_summary.loc[weekly_summary["Week"] <= 4].copy()

# Plot the weekly trend.
fig, ax = plt.subplots(figsize=(11, 7))

ax.plot(
    weekly_summary["Week"],
    weekly_summary["MedianChange"],
    marker="o",
    markersize=9,
    linewidth=2.5,
    color="#2563EB"
)

# Label sample size at each point.
for _, row in weekly_summary.iterrows():
    ax.annotate(
        f'n={int(row["Observations"])}',
        (row["Week"], row["MedianChange"]),
        xytext=(0, 12),
        textcoords="offset points",
        ha="center",
        fontsize=10
    )

ax.axhline(
    0,
    color="#64748B",
    linestyle="--",
    linewidth=1.3
)

ax.set_title(
    "How does performance change throughout the season?",
    fontsize=18,
    loc="left",
    pad=20
)

ax.set_xlabel(
    "Weeks since first recorded event performance",
    fontsize=12
)

ax.set_ylabel(
    "Median performance change from opening mark (%)",
    fontsize=12
)

ax.xaxis.set_major_locator(MaxNLocator(integer=True))
ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))

ax.grid(alpha=0.2)
ax.set_axisbelow(True)
ax.spines[["top", "right"]].set_visible(False)
ax.margins(x=0.04, y=0.2)

fig.subplots_adjust(
    left=0.12,
    right=0.96,
    bottom=0.23,
    top=0.86
)

fig.text(
    0.12, 0.035,
    "Holy Cross Track & Field, 2026. "
    "Week 0 begins with each athlete's first recorded date in an event.\n"
    "Each athlete-event contributes at most one observation per week. "
    "Positive values indicate improvement.\n"
    "Weekly groups may contain different athletes and events; "
    "this is not a continuous team-wide average.",
    fontsize=9,
    color="#475569"
)

finish_chart(
    fig,
    "weekly_performance_development_2026.png"
)

print("\nWeekly performance development:")
print(weekly_summary.round(2).to_string(index=False))


# ============================================================
# GRAPH: COMPETITION APPEARANCES VS. IMPROVEMENT
# ============================================================

# Focus on the 2026 season.
competition_data = improvement.loc[
    improvement["Season"] == 2026
].copy()

# One observation per athlete-event.
competition_data = competition_data.dropna(
    subset=["DatesRecorded", "ImprovementPercent"]
)

x = competition_data["DatesRecorded"].to_numpy(dtype=float)
y = competition_data["ImprovementPercent"].to_numpy(dtype=float)

n = len(competition_data)

if n >= 3 and np.ptp(x) > 0 and np.ptp(y) > 0:

    # Pearson correlation and regression.
    r = np.corrcoef(x, y)[0, 1]
    slope, intercept = np.polyfit(x, y, 1)

    x_line = np.linspace(x.min(), x.max(), 100)
    y_line = slope * x_line + intercept

    fig, ax = plt.subplots(figsize=(11, 7))

    # Jitter prevents athletes with the same number of
    # appearances from completely overlapping.
    rng = np.random.default_rng(42)
    x_jitter = x + rng.uniform(-0.15, 0.15, size=n)

    ax.scatter(
        x_jitter,
        y,
        color="#2563EB",
        s=75,
        alpha=0.65,
        edgecolors="white",
        linewidth=0.7,
        label="Athlete-event"
    )

    ax.plot(
        x_line,
        y_line,
        color="#0F172A",
        linestyle="--",
        linewidth=2,
        label="Linear best-fit line"
    )

    ax.text(
        0.04, 0.96,
        f"Pearson r = {r:.3f}\nn = {n}",
        transform=ax.transAxes,
        va="top",
        fontsize=12,
        bbox=dict(
            facecolor="white",
            edgecolor="#CBD5E1",
            boxstyle="round,pad=0.6"
        )
    )

    ax.set_title(
        "Does competing more often relate to greater improvement?",
        fontsize=17,
        loc="left",
        pad=20
    )

    ax.set_xlabel(
        "Number of meet dates recorded in the event",
        fontsize=12
    )

    ax.set_ylabel(
        "Improvement from opening mark to season best (%)",
        fontsize=12
    )

    ax.set_xticks(
        range(int(x.min()), int(x.max()) + 1)
    )

    ax.yaxis.set_major_formatter(
        PercentFormatter(xmax=100)
    )

    ax.set_xlim(x.min() - 0.5, x.max() + 0.5)
    ax.grid(alpha=0.2)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper right", frameon=False)

    fig.subplots_adjust(
        left=0.12,
        right=0.97,
        bottom=0.23,
        top=0.86
    )

    fig.text(
        0.12, 0.035,
        "Holy Cross Track & Field, 2026. One dot per athlete-event with at least two meet dates.\n"
        "Positive values indicate improvement. Dots are shifted horizontally for visibility.\n"
        "More appearances create more opportunities for a personal best; correlation does not establish causation.",
        fontsize=9,
        color="#475569"
    )

    finish_chart(
        fig,
        "competition_frequency_vs_improvement_2026.png"
    )

    print("\nCompetition frequency vs. improvement:")
    print(f"Pearson r = {r:.3f}")
    print(f"Observations = {n}")

    # Show median improvement for each number of appearances.
    frequency_summary = (
        competition_data.groupby("DatesRecorded")
        .agg(
            Observations=("AthleteID", "size"),
            MedianImprovement=("ImprovementPercent", "median")
        )
        .round(2)
    )

    print(frequency_summary.to_string())

else:
    print("Insufficient variation for competition-frequency correlation.")


# ============================================================
# GRAPH: WITHIN-SEASON IMPROVEMENT BY GRADE LEVEL
# ============================================================

# Focus on 2026.
grade_data = improvement.loc[
    improvement["Season"] == 2026
].copy()

# Add grade for each athlete in the 2026 season.
grade_data = grade_data.merge(
    athlete_seasons[["AthleteID", "Season", "Grade"]],
    on=["AthleteID", "Season"],
    how="left",
    validate="many_to_one"
)

require(
    grade_data["Grade"].notna().all(),
    "Missing grade for an athlete in the grade analysis."
)

# One observation per athlete: median improvement across events.
athlete_grade_improvement = (
    grade_data.groupby(["AthleteID", "Grade"])
    .agg(
        MedianImprovement=("ImprovementPercent", "median"),
        EventsMeasured=("Event", "nunique")
    )
    .reset_index()
)

grades = [5, 6, 7, 8]

grade_groups = [
    athlete_grade_improvement.loc[
        athlete_grade_improvement["Grade"] == grade,
        "MedianImprovement"
    ].to_numpy(dtype=float)
    for grade in grades
]

# Remove empty grades if necessary.
plot_groups = [
    (grade, values)
    for grade, values in zip(grades, grade_groups)
    if len(values) > 0
]

plot_grades = [item[0] for item in plot_groups]
plot_values = [item[1] for item in plot_groups]

fig, ax = plt.subplots(figsize=(11, 7))

# Box plots show median, quartiles, and spread.
ax.boxplot(
    plot_values,
    positions=range(1, len(plot_grades) + 1),
    widths=0.5,
    patch_artist=True,
    showfliers=False,
    boxprops=dict(facecolor="#DBEAFE", edgecolor="#2563EB"),
    medianprops=dict(color="#0F172A", linewidth=2.5),
    whiskerprops=dict(color="#2563EB"),
    capprops=dict(color="#2563EB")
)

# Add individual athlete observations.
rng = np.random.default_rng(42)

for position, values in enumerate(plot_values, start=1):
    jitter = rng.uniform(-0.13, 0.13, size=len(values))

    ax.scatter(
        position + jitter,
        values,
        color="#2563EB",
        s=65,
        alpha=0.75,
        edgecolors="white",
        linewidth=0.7,
        zorder=3
    )

# Label grades and sample sizes.
labels = [
    f"Grade {grade}\n{len(values)} athletes"
    for grade, values in plot_groups
]

ax.set_xticks(
    range(1, len(plot_grades) + 1),
    labels=labels
)

ax.set_title(
    "Does within-season improvement differ by grade?",
    fontsize=18,
    loc="left",
    pad=20
)

ax.set_xlabel("Grade level", fontsize=12)
ax.set_ylabel(
    "Athlete's median improvement across events (%)",
    fontsize=12
)

ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))

ax.grid(axis="y", alpha=0.2)
ax.set_axisbelow(True)
ax.spines[["top", "right"]].set_visible(False)

fig.subplots_adjust(
    left=0.12,
    right=0.97,
    bottom=0.24,
    top=0.86
)

fig.text(
    0.12, 0.035,
    "Holy Cross Track & Field, 2026. One observation per athlete.\n"
    "Each athlete's value is the median improvement across events with at least two recorded dates.\n"
    "Season best includes the opening mark; grade groups may differ in event mix and experience.",
    fontsize=9,
    color="#475569"
)

finish_chart(
    fig,
    "improvement_by_grade_2026.png"
)

print("\nImprovement by grade:")
print(
    athlete_grade_improvement.groupby("Grade")["MedianImprovement"]
    .agg(["count", "median", "mean"])
    .round(2)
    .to_string()
)


# ============================================================
# GRAPH: STATE-STANDARD ATTAINMENT, 2024-2026
# ============================================================

# Count distinct athletes who met or exceeded at least
# one grade-based Class 1A performance benchmark.

roster_counts = (
    athlete_seasons
    .groupby("Season")["AthleteID"]
    .nunique()
    .rename("RosterSize")
)

benchmark_athletes = (
    meeting_standard
    .groupby("Season")["AthleteID"]
    .nunique()
    .rename("AthletesMeetingStandard")
)

benchmark_summary = (
    pd.DataFrame(index=sorted(results["Season"].unique()))
    .join(roster_counts)
    .join(benchmark_athletes)
)

benchmark_summary["AthletesMeetingStandard"] = (
    benchmark_summary["AthletesMeetingStandard"]
    .fillna(0)
    .astype(int)
)

require(
    benchmark_summary["RosterSize"].notna().all(),
    "Missing roster sizes for benchmark analysis."
)

require(
    (benchmark_summary["AthletesMeetingStandard"]
     <= benchmark_summary["RosterSize"]).all(),
    "Benchmark athlete count exceeds roster size."
)

benchmark_summary["PercentMeetingStandard"] = (
    benchmark_summary["AthletesMeetingStandard"]
    / benchmark_summary["RosterSize"] * 100
)

benchmark_summary.index.name = "Season"

print("\nTHREE-SEASON STATE BENCHMARK COMPARISON")
print(benchmark_summary.round(2).to_string())


# ------------------------------------------------------------
# CREATE TWO-PANEL VISUALIZATION
# ------------------------------------------------------------

seasons = benchmark_summary.index.astype(int).tolist()
positions = np.arange(len(seasons))

counts = benchmark_summary["AthletesMeetingStandard"]
percentages = benchmark_summary["PercentMeetingStandard"]

fig, (ax_count, ax_pct) = plt.subplots(
    1, 2,
    figsize=(14, 6.5),
    gridspec_kw={"width_ratios": [1, 1.15]}
)

# LEFT: NUMBER OF ATHLETES MEETING BENCHMARKS

bars = ax_count.bar(
    positions,
    counts,
    width=0.55,
    color="#2563EB"
)

for bar, count in zip(bars, counts):
    ax_count.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height() + 0.12,
        str(int(count)),
        ha="center",
        fontsize=13,
        fontweight="bold"
    )

ax_count.set_xticks(positions, seasons)
ax_count.set_ylabel("Number of athletes")
ax_count.set_title(
    "How many athletes met a standard?",
    fontsize=14,
    pad=17
)
ax_count.set_ylim(0, max(1, counts.max()) * 1.35)
ax_count.yaxis.set_major_locator(MaxNLocator(integer=True))


# RIGHT: PERCENTAGE OF THE ROSTER

ax_pct.plot(
    positions,
    percentages,
    color="#15803D",
    linewidth=2.5,
    marker="o",
    markersize=11
)

for pos, (_, row) in enumerate(benchmark_summary.iterrows()):
    ax_pct.annotate(
        f'{row["PercentMeetingStandard"]:.1f}%'
        f' ({int(row["AthletesMeetingStandard"])}/'
        f'{int(row["RosterSize"])})',
        (pos, row["PercentMeetingStandard"]),
        xytext=(0, 14),
        textcoords="offset points",
        ha="center",
        fontsize=11
    )

ax_pct.set_xticks(positions, seasons)
ax_pct.set_ylabel("Percentage of roster (%)")
ax_pct.set_title(
    "What share of the roster met a standard?",
    fontsize=14,
    pad=17
)
ax_pct.set_ylim(0, max(5, percentages.max() * 1.4))
ax_pct.yaxis.set_major_formatter(PercentFormatter(xmax=100))


# CONSISTENT STYLING

for ax in (ax_count, ax_pct):
    ax.grid(axis="y", alpha=0.2)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_xlabel("Season")

fig.suptitle(
    "State-standard attainment across three seasons",
    fontsize=20,
    y=0.98
)

fig.subplots_adjust(
    left=0.09,
    right=0.97,
    top=0.79,
    bottom=0.27,
    wspace=0.30
)

fig.text(
    0.09, 0.045,
    "Holy Cross Track & Field, 2024-2026. "
    "Each athlete is counted at most once per season.\n"
    "Benchmarks use IESA Class 1A grade-based individual-event standards. "
    "Roster percentages use listed athletes.\n"
    "Meeting a benchmark is not official state qualification. "
    "Meet coverage and roster composition vary across seasons.",
    fontsize=9.5,
    color="#475569"
)

finish_chart(
    fig,
    "state_standard_attainment_2024_2026.png"
)

# ============================================================
# GRAPH: ROSTER COMPOSITION BY GRADE AND SEASON
# ============================================================

# Count unique athletes by season and grade.
grade_roster = (
    athlete_seasons.loc[
        athlete_seasons["Season"].isin([2024, 2025, 2026])
    ]
    .groupby(["Season", "Grade"])["AthleteID"]
    .nunique()
    .reset_index(name="Athletes")
)

# Pivot so each grade becomes a column.
grade_roster_pivot = (
    grade_roster.pivot(
        index="Season",
        columns="Grade",
        values="Athletes"
    )
    .fillna(0)
    .astype(int)
    .sort_index()
)

# Make sure all expected grades appear as columns.
for grade in [5, 6, 7, 8]:
    if grade not in grade_roster_pivot.columns:
        grade_roster_pivot[grade] = 0

grade_roster_pivot = grade_roster_pivot[[5, 6, 7, 8]]

print("\nROSTER COMPOSITION BY GRADE")
print(grade_roster_pivot.to_string())

# Plot stacked bars.
fig, ax = plt.subplots(figsize=(11, 7))

seasons = grade_roster_pivot.index.astype(int).tolist()
positions = np.arange(len(seasons))

grade_labels = {
    5: "Grade 5",
    6: "Grade 6",
    7: "Grade 7",
    8: "Grade 8"
}

grade_colors = {
    5: "#DBEAFE",
    6: "#93C5FD",
    7: "#3B82F6",
    8: "#1D4ED8"
}

bottom = np.zeros(len(seasons))

for grade in [5, 6, 7, 8]:
    values = grade_roster_pivot[grade].to_numpy()

    bars = ax.bar(
        positions,
        values,
        bottom=bottom,
        width=0.6,
        color=grade_colors[grade],
        label=grade_labels[grade]
    )

    # Label larger segments inside the bars.
    for bar, value, current_bottom in zip(bars, values, bottom):
        if value > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                current_bottom + value / 2,
                str(int(value)),
                ha="center",
                va="center",
                fontsize=11,
                color="white" if grade in [7, 8] else "#0F172A",
                fontweight="bold"
            )

    bottom += values

# Add total roster labels above each bar.
totals = grade_roster_pivot.sum(axis=1).to_numpy()

for pos, total in zip(positions, totals):
    ax.text(
        pos,
        total + 0.6,
        f"{int(total)} athletes",
        ha="center",
        fontsize=13,
        fontweight="bold"
    )

ax.set_title(
    "How did roster age composition change across seasons?",
    fontsize=18,
    loc="left",
    pad=20
)

ax.set_xlabel("Season", fontsize=12)
ax.set_ylabel("Number of athletes", fontsize=12)
ax.set_xticks(positions, seasons)

ax.grid(axis="y", alpha=0.2)
ax.set_axisbelow(True)
ax.spines[["top", "right"]].set_visible(False)

ax.legend(
    title="Grade",
    frameon=False,
    loc="upper left"
)

ax.set_ylim(0, totals.max() * 1.18)

fig.subplots_adjust(
    left=0.11,
    right=0.97,
    bottom=0.20,
    top=0.86
)

fig.text(
    0.11, 0.045,
    "Holy Cross Track & Field, 2024-2026. Bars show listed roster size by grade level in each season.\n"
    "This graph is intended to help interpret state-standard attainment alongside changes in roster age structure.",
    fontsize=9,
    color="#475569"
)

finish_chart(
    fig,
    "roster_composition_by_grade.png"
)


# ============================================================
# STATE BENCHMARK ATTAINMENT BY GRADE GROUP
# ============================================================

# Count roster athletes by season and grade group.
roster_grade = athlete_seasons.loc[
    athlete_seasons["Season"].isin([2024, 2025, 2026])
].copy()

roster_grade["GradeGroup"] = np.where(
    roster_grade["Grade"].isin([5, 6]),
    "Grades 5-6",
    "Grades 7-8"
)

roster_group_counts = (
    roster_grade.groupby(["Season", "GradeGroup"])["AthleteID"]
    .nunique()
    .rename("RosterSize")
)

# Find unique athletes who met at least one standard.
qualified_grade = (
    results.loc[results["MeetsStandard"]]
    [["Season", "AthleteID", "Grade"]]
    .drop_duplicates()
    .copy()
)

qualified_grade["GradeGroup"] = np.where(
    qualified_grade["Grade"].isin([5, 6]),
    "Grades 5-6",
    "Grades 7-8"
)

qualified_counts = (
    qualified_grade.groupby(["Season", "GradeGroup"])["AthleteID"]
    .nunique()
    .rename("AthletesMeetingStandard")
)

grade_benchmark_summary = (
    pd.concat([roster_group_counts, qualified_counts], axis=1)
    .fillna({"AthletesMeetingStandard": 0})
)

grade_benchmark_summary["AthletesMeetingStandard"] = (
    grade_benchmark_summary["AthletesMeetingStandard"].astype(int)
)

grade_benchmark_summary["PercentMeetingStandard"] = (
    grade_benchmark_summary["AthletesMeetingStandard"]
    / grade_benchmark_summary["RosterSize"] * 100
)

print("\nSTATE BENCHMARK ATTAINMENT BY GRADE GROUP")
print(grade_benchmark_summary.round(2).to_string())
