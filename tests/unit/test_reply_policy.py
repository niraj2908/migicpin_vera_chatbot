from vera.decision.reply_policy import decide_reply


def test_first_auto_reply_waits() -> None:
    decision = decide_reply(
        "Thank you for contacting SK Pizza Junction! Our team will respond shortly.",
        previous_message=None,
        auto_reply_hits_so_far=0,
    )
    assert decision.action == "wait"
    assert decision.kind == "auto_reply"
    assert decision.wait_seconds is not None and decision.wait_seconds > 0


def test_second_auto_reply_ends() -> None:
    decision = decide_reply(
        "Thank you for contacting SK Pizza Junction! Our team will respond shortly.",
        previous_message="Thank you for contacting SK Pizza Junction! Our team will respond shortly.",
        auto_reply_hits_so_far=1,
    )
    assert decision.action == "end"
    assert decision.kind == "auto_reply"


def test_verbatim_repeat_without_known_phrase_is_treated_as_auto_reply() -> None:
    decision = decide_reply(
        "ok thanks bye",
        previous_message="ok thanks bye",
        auto_reply_hits_so_far=0,
    )
    assert decision.kind == "auto_reply"


def test_hostile_message_ends_and_flags_kind() -> None:
    decision = decide_reply("Stop messaging me. This is useless spam.", None, 0)
    assert decision.action == "end"
    assert decision.kind == "hostile_optout"


def test_intent_commitment_switches_to_action_mode() -> None:
    decision = decide_reply("Ok let's do it. What's next?", None, 0)
    assert decision.action == "send"
    assert decision.kind == "intent_commit"
    assert decision.reply_intent == "accept_and_advance"


def test_curveball_message_redirects_without_ending() -> None:
    decision = decide_reply("Btw can you also help me with my GST filing?", None, 0)
    assert decision.action == "send"
    assert decision.kind == "other"
    assert decision.reply_intent == "redirect_to_original_ask"


def test_engaged_accept_message_is_not_misclassified_as_hostile() -> None:
    decision = decide_reply("Yes please send the abstract, sounds good", None, 0)
    assert decision.action == "send"
    assert decision.kind == "intent_commit"
