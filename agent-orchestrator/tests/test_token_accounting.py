from regulatory_agent.models import QwenModel, build_messages
from regulatory_agent.contracts import Plan


def test_qwen_counts_the_same_messages_and_thinking_template_as_inference(monkeypatch):
    calls = []
    class Tokenizer:
        def apply_chat_template(self, messages, **options):
            calls.append((messages, options))
            return [1, 2, 3, 4]
    monkeypatch.setattr('regulatory_agent.models.load_tokenizer', lambda name: Tokenizer())
    monkeypatch.setenv('QWEN_DISABLE_THINKING', '1')
    monkeypatch.setenv('QWEN_TOKENIZER', '/test/tokenizer')
    model = QwenModel(base_url='http://127.0.0.1:1/v1', api_key='test-only')
    payload = {'question':'What is the policy?', 'messages':[{'role':'user','content':'Earlier question'}]}
    try:
        assert model.tokenizer_name == '/test/tokenizer'
        assert model.count_input_tokens('planner', payload, Plan) == 4
        assert calls == [(build_messages('planner', payload, Plan), {
            'tokenize':True, 'add_generation_prompt':True, 'enable_thinking':False})]
        model.disable_thinking = False
        model.count_input_tokens('planner', payload, Plan)
        assert 'enable_thinking' not in calls[-1][1]
    finally:
        model.client.close()
