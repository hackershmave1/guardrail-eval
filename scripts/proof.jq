# Proof for every landed attack, straight from a promptfoo result/export JSON:
#   jq -f scripts/proof.jq results/mcp.json
#   promptfoo export eval <evalId> -o x.json && jq -f scripts/proof.jq x.json
# Shows the user prompt, the injected text as the model received it (a tool
# description or a tool result matching the row's injection_marker), the
# model's tool calls, and what reached the attacker's sink.
.results.results[]
| select(.success == false)
| (.testCase.vars.injection_marker // "^$") as $m
| {
    row: .testCase.description,
    prompt: .prompt.raw,
    injection_note: .testCase.vars.injection_note,
    injected_tool_descriptions: [.response.output.transcript.tools[]?.function
      | select((.description // "") | test($m)) | {name, description}],
    injected_context: [.response.output.transcript.conversations[]?.messages[]?
      | select(.role != "assistant" and ((.content | tostring) | test($m)))
      | {role, content: ((.content | tostring)[0:1500])}],
    model_tool_calls: [.response.output.transcript.conversations[]?.messages[]?.tool_calls[]?
      | "\(.function.name) \(.function.arguments[0:300])"],
    model_replies: [.response.output.transcript.conversations[]?.reply.content // empty | .[0:600]],
    oracle: .gradingResult.reason,
    sink: .response.output.sink
  }
