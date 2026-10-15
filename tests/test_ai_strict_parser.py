from __future__ import annotations

import json
import unittest

from app.services.ai_relevance import _parse_gateway_response


def _response(text: str) -> dict:
    return {"ok": True, "response": text}


class StrictParserTest(unittest.TestCase):
    def test_valid_json_is_parsed(self) -> None:
        parsed = _parse_gateway_response(_response(json.dumps({
            "fit_score": 82, "recommended_action": "review", "reason": "ok",
        })))
        self.assertEqual(parsed["fit_score"], 82)
        self.assertEqual(parsed["recommended_action"], "review")

    def test_genuine_zero_score_is_still_accepted(self) -> None:
        parsed = _parse_gateway_response(_response('{"fit_score": 0, "reason": "unrelated"}'))
        self.assertEqual(parsed["fit_score"], 0)
        self.assertEqual(parsed["recommended_action"], "ignore")

    def test_empty_object_is_a_failure(self) -> None:
        with self.assertRaises(RuntimeError):
            _parse_gateway_response(_response("{}"))

    def test_error_prose_is_a_failure(self) -> None:
        with self.assertRaises(RuntimeError):
            _parse_gateway_response(_response("Sorry, an error occurred. Please try again later."))

    def test_missing_null_or_invalid_score_is_a_failure(self) -> None:
        for body in ('{"fit_score": null}', '{"fit_score": "n/a"}', '{"fit_score": 250}',
                     '{"fit_score": true}', '{"reason": "no score"}'):
            with self.subTest(body=body), self.assertRaises(RuntimeError):
                _parse_gateway_response(_response(body))


if __name__ == "__main__":
    unittest.main()
