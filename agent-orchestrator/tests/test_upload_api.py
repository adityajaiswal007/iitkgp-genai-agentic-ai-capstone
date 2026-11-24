import base64

from fastapi.testclient import TestClient
from regulatory_agent.api import create_app
from regulatory_agent.config import Settings


def upload(name='report.csv', mime='text/csv', content=b'item,days\nRBI demo retention period,8472\n'):
    return {'name':name, 'mime_type':mime, 'content_base64':base64.b64encode(content).decode()}


def test_uploaded_text_is_cited_and_survives_followups_without_leaking_to_other_chats(tmp_path):
    config = Settings(database_path=tmp_path/'state.sqlite')
    with TestClient(create_app(config)) as client:
        response = client.post('/v1/executions/attachments', json={
            'conversation_id':'files-chat', 'question':'What is the RBI demo retention period in the attachment?',
            'attachments':[upload()],
        })
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'answered'
        assert '8472' in data['answer_text']
        assert data['answer']['sources'][0]['authority'] == 'UPLOAD'
        assert data['answer']['sources'][0]['title'] == 'report.csv'
    with TestClient(create_app(config)) as client:
        data = client.post('/v1/executions', json={
            'conversation_id':'files-chat', 'question':'What is the RBI demo retention period in report.csv?',
        }).json()
        assert data['status'] == 'answered' and '8472' in data['answer_text']
        isolated = client.post('/v1/executions', json={
            'conversation_id':'another-chat', 'question':'What is the RBI demo retention period in report.csv?',
        }).json()
        assert '8472' not in isolated['answer_text']
        assert isolated['answer']['sources'] == []


def test_malformed_attachments_fail_explicitly_without_echoing_file_contents(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path/'state.sqlite'))) as client:
        for attachment in (upload(content=b'not-a-real-pdf',name='bad.pdf',mime='application/pdf'),
                           {'name':'report.csv','mime_type':'text/csv','content_base64':'not-base64!'},
                           upload(name='../secrets.txt',mime='text/plain')):
            response = client.post('/v1/executions/attachments', json={
                'question':'Read the attachment', 'attachments':[attachment],
            })
            assert response.status_code == 422
            assert 'not-a-real-pdf' not in response.text
            assert 'not-base64!' not in response.text


def test_an_answer_using_only_an_old_file_cannot_pass_for_a_new_upload(tmp_path):
    from unittest.mock import patch
    from regulatory_agent.models import DemoModel, REVIEW_FIELDS
    old = b'RBI demo retention period: 7643 days'

    class CitesOlderFile(DemoModel):
        def complete(self, role, payload, *args):
            if role == 'executor':
                import json
                passage = next(p for p in payload['evidence'] if p['title']=='old.txt')
                return json.dumps({'claims':[
                    {'text':passage['text'], 'citations':[passage['citation_id']]},
                ]}), 100
            if role == 'critic':
                import json
                return json.dumps({'decision':'accept','checks':dict.fromkeys(REVIEW_FIELDS, 'pass')}), 100
            return super().complete(role,payload,*args)

    with patch('regulatory_agent.api.DemoModel', CitesOlderFile):
        with TestClient(create_app(Settings(database_path=tmp_path/'state.sqlite',max_revisions=0))) as client:
            response = client.post('/v1/executions/attachments',json={
                'question':'What is the RBI demo retention period in the newly attached file?',
                'attachments':[dict(upload('old.txt','text/plain',old),current=False),
                               dict(upload('new.txt','text/plain',b'RBI demo retention period: 8472 days'),current=True)],
            })
            data = response.json()
            assert response.status_code == 200
            assert data['status'] != 'answered'
            assert data['answer']['claims'] == []


def test_new_file_drafts_and_reviews_do_not_treat_prior_answers_as_current_evidence(tmp_path):
    from unittest.mock import patch
    from regulatory_agent.models import DemoModel
    seen = {}
    class RecordingModel(DemoModel):
        def complete(self, role, payload, *args):
            seen[role] = payload.get('messages')
            return super().complete(role, payload, *args)
    history = [{'role':'user','content':'Read an old RBI demo circular'},
               {'role':'assistant','content':'The old demo circular says 7643 days.'}]
    with patch('regulatory_agent.api.DemoModel',RecordingModel):
        with TestClient(create_app(Settings(database_path=tmp_path/'state.sqlite'))) as client:
            response = client.post('/v1/executions/attachments',json={
                'question':'What is the RBI demo retention period in the newly attached file?', 'history':history,
                'attachments':[upload()],
            })
            assert response.json()['status']=='answered'
    assert seen['planner'] == history
    assert seen['executor'] == []
    assert seen['critic'] == []
