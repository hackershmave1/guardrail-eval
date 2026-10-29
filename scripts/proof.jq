# Proof for every landed attack, straight from a promptfoo result/export JSON:
#   jq -f scripts/proof.jq results/mcp.json
#   promptfoo export eval <evalId> -o x.json && jq -f scripts/proof.jq x.json
# Three sections, keyed on the provider's `outcome` (promptfoo/providers/outcome.py):
#   landed          LANDED: the user prompt, the injected text as the model
#                   received it (a tool description or a message matching the
#                   row's injection_marker), the model's tool calls, the sink
#   false_positive  FALSE_POSITIVE: the benign control and what blocked it
#   not_run         NOT_RUN / NOT_DELIVERED: why the trial can't be judged
# A provider error leaves `output` a string (or absent); it reads as {} and lands in not_run.
[.results.results[] | (.response.output |= (if type == "object" then . else {} end))
  | {r: ., o: (.response.output.outcome // "ERROR")}] as $all
| {
  landed: [$all[] | select(.o == "LANDED") | .r
    | (.testCase.vars.injection_marker // "^$") as $m
    | {
        row: .testCase.description,
        prompt: .prompt.raw,
        delivered: .response.output.delivered,
        detected: .response.output.detected,
        injection_note: .testCase.vars.injection_note,
        injected_tool_descriptions: [.response.output.transcript.tools[]?.function
          | select((.description // "") | test($m)) | {name, description}],
        injected_context: [.response.output.transcript.conversations[]?.messages[]?
          | select(.role != "assistant" and ((.content | tostring) | test($m)))
          | {role, content: ((.content | tostring)[0:1500])}],
        model_tool_calls: [.response.output.transcript.conversations[]?.messages[]?.tool_calls[]?
          | "\(.function.name) \(.function.arguments[0:300])"],
        model_replies: [.response.output.transcript.conversations[]?.reply.content // empty | .[0:600]],
        events: .response.output.events,
        oracle: .gradingResult.reason,
        sink: .response.output.sink,
        evidence_attempts: (.response.output.evidence_attempts // [])
      }],
  false_positive: [$all[] | select(.o == "FALSE_POSITIVE") | .r
    | {row: .testCase.description, prompt: .prompt.raw,
       events: .response.output.events, reason: .gradingResult.reason,
       evidence_attempts: (.response.output.evidence_attempts // [])}],
  not_run: [$all[] | select(.o == "NOT_RUN" or .o == "NOT_DELIVERED" or .o == "ERROR") | .r
    | {row: .testCase.description, outcome: (.response.output.outcome // "ERROR"),
       reason: (.gradingResult.reason // .error), resamples: .response.output.resamples,
       evidence_attempts: (.response.output.evidence_attempts // [])}]
}
