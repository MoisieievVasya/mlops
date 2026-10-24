"""Contract tests — the ones that fail when a config file and the code disagree.

These do not touch the database or the stack. They check the three files that are
contracts rather than settings (taxonomy, features, rules) against the code that
reads them, because a silent disagreement between those files and the pipeline is the
failure mode none of the runtime checks can see.
"""

from __future__ import annotations

import yaml

from newsflow.config import FEATURES_PATH, RULES_PATH, TAXONOMY_PATH
from newsflow.etl import extract
from newsflow.etl.features import feature_version
from newsflow.taxonomy import UNMAPPED, label_map, serving_categories, to_serving
from newsflow.text import NO_DEK, hash_author, model_text, normalise, normalise_dek, word_count

# The 42 labels observed in News Category Dataset v3. If the archive ever delivers a
# label outside this list, the quality gate catches it at runtime — this test catches
# it at commit time instead.
_TAXONOMY = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))
ARCHIVE_RAW_LABELS = set(_TAXONOMY["map"])


class TestTaxonomy:
    def test_every_raw_label_maps_into_the_serving_set(self):
        categories = set(serving_categories())
        for raw, serving in label_map().items():
            assert serving in categories, (
                f"{raw} maps to {serving}, which is not a serving category"
            )

    def test_the_serving_set_is_exactly_seventeen(self):
        # Design 5.2 freezes the list at 17. Changing it is a product decision and a
        # change to the response contract (6.8), so it should break a test first.
        assert len(serving_categories()) == 17
        assert len(set(serving_categories())) == 17

    def test_every_serving_category_has_at_least_one_raw_label(self):
        covered = set(label_map().values())
        missing = set(serving_categories()) - covered
        assert not missing, f"no raw label maps to {missing} — the class would never be trained"

    def test_all_forty_two_archive_labels_are_covered(self):
        assert len(ARCHIVE_RAW_LABELS) == 42

    def test_an_unknown_label_becomes_unmapped_and_is_not_guessed(self):
        assert to_serving("CRYPTO") == UNMAPPED
        assert to_serving(None) == UNMAPPED
        assert to_serving("") == UNMAPPED

    def test_mapping_is_case_and_whitespace_insensitive(self):
        assert to_serving("  politics ") == to_serving("POLITICS") == "POLITICS"


class TestFeatureContract:
    @staticmethod
    def _spec() -> dict:
        return yaml.safe_load(FEATURES_PATH.read_text(encoding="utf-8"))

    def test_every_feature_declares_a_serving_time_source(self):
        # Design 5.3: "A feature without a serving-time source scores as incomplete,
        # because that is exactly the feature that will differ between training and
        # serving." This test is that rule, enforced.
        for feature in self._spec()["features"]:
            assert feature.get("serving_source"), f"{feature['name']} has no serving-time source"
            assert feature.get("training_source"), f"{feature['name']} has no training-time source"

    def test_the_leakage_field_is_documented_as_dropped(self):
        dropped = {entry["name"] for entry in self._spec()["dropped"]}
        assert "link" in dropped

    def test_feature_version_is_stable_and_derived_from_the_contract(self):
        feature_version.cache_clear()
        first = feature_version()
        feature_version.cache_clear()
        assert feature_version() == first
        assert first.startswith("fv1.")


class TestWaivers:
    """A waiver is bounded in time and in scope, or it is just a weaker threshold."""

    def test_the_dek_waiver_covers_only_the_episode_it_names(self):
        from datetime import date

        from newsflow.etl.quality import waiver_for

        assert waiver_for("Q6", date(2014, 10, 1)) is not None
        assert waiver_for("Q6", date(2015, 6, 1)) is not None
        # One month either side of the window, the check is unchanged.
        assert waiver_for("Q6", date(2014, 4, 1)) is None
        assert waiver_for("Q6", date(2015, 7, 1)) is None

    def test_a_waiver_does_not_leak_to_other_checks(self):
        from datetime import date

        from newsflow.etl.quality import waiver_for

        # Q4 is the headline check; nothing waives it, in any month.
        assert waiver_for("Q4", date(2014, 10, 1)) is None

    def test_every_waiver_carries_a_reason_and_an_approver(self):
        from newsflow.etl.quality import contract

        for waiver in contract().get("waivers") or []:
            assert waiver.get("reason"), f"{waiver['id']} has no reason"
            assert waiver.get("approved_by"), f"{waiver['id']} has no approver"
            assert waiver["from"] <= waiver["to"]


class TestRuleLexicon:
    def test_rule_categories_are_serving_categories(self):
        spec = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
        categories = set(serving_categories())
        for name in spec["categories"]:
            assert name in categories, f"the lexicon names {name}, which is not a serving category"


class TestSharedTextTransform:
    """The one transform both the training path and the serving path import."""

    def test_normalise_is_idempotent(self):
        once = normalise("  The  Ünicode   Headline ")
        assert normalise(once) == once

    def test_normalise_strips_urls(self):
        assert "http" not in normalise("read more at https://example.com/story now")

    def test_a_missing_dek_is_encoded_not_imputed(self):
        assert normalise_dek(None) == NO_DEK
        assert normalise_dek("   ") == NO_DEK
        assert normalise_dek("A real dek") == "a real dek"

    def test_the_no_dek_token_is_not_counted_as_content(self):
        assert word_count("three word headline", NO_DEK) == 3

    def test_model_text_is_one_definition(self):
        assert model_text("headline", "dek") == "headline dek"

    def test_the_byline_hash_is_salted_and_never_reversible_to_the_name(self):
        name = "Jane Reporter"
        assert hash_author(name, "salt-a") != hash_author(name, "salt-b")
        assert hash_author(name, "salt-a") == hash_author(" jane reporter ", "salt-a")
        assert name.lower() not in hash_author(name, "salt-a")

    def test_an_absent_byline_is_its_own_bucket(self):
        assert hash_author(None, "salt") == hash_author("", "salt") == "anon"


class TestFaultInjection:
    """The faults must really break the data — a flag the gate reads proves nothing."""

    @staticmethod
    def _batch() -> list[dict]:
        return [
            {
                "headline": f"headline {i}",
                "short_description": "dek",
                "category": "POLITICS",
                "date": "2022-03-01",
                "link": f"https://example.com/{i}",
                "authors": "A",
            }
            for i in range(40)
        ]

    def test_none_is_a_no_op(self):
        rows = self._batch()
        assert extract.inject_fault(rows, "none") == rows

    def test_unmapped_label_introduces_a_label_the_taxonomy_lacks(self):
        broken = extract.inject_fault(self._batch(), "unmapped_label")
        assert any(to_serving(row["category"]) == UNMAPPED for row in broken)

    def test_null_headline_empties_real_rows(self):
        broken = extract.inject_fault(self._batch(), "null_headline")
        assert any(not row["headline"] and not row["short_description"] for row in broken)

    def test_duplicate_rows_really_duplicates(self):
        broken = extract.inject_fault(self._batch(), "duplicate_rows")
        assert len(broken) == 50

    def test_truncate_batch_really_truncates(self):
        assert len(extract.inject_fault(self._batch(), "truncate_batch")) == 10

    def test_an_unknown_fault_is_refused(self):
        import pytest

        with pytest.raises(ValueError, match="unknown fault"):
            extract.inject_fault(self._batch(), "make_it_worse")
