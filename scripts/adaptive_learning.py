"""ChatGPT運用の読み取り・検証補助。アプリ/LLM/API/本番書込みCLIではない。"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import pathlib
import re
import uuid

JST = dt.timezone(dt.timedelta(hours=9))
ROOT = pathlib.Path(__file__).resolve().parents[1]
BIO = {
    'Q-BIO-0001-1': ('BIO-0001/phospholipid', 'リン脂質の構造・両親媒性'),
    'Q-BIO-0001-2': ('BIO-0001/bilayer', '脂質二重層の配向'),
    'Q-BIO-0001-3': ('BIO-0001/glycan', '糖鎖の局在・輸送'),
    'Q-BIO-0001-4': ('BIO-0001/protein', '膜タンパク質の機能'),
    'Q-BIO-0001-5': ('BIO-0001/mosaic', '流動モザイクモデル'),
}
GAPS = ('わかんない', 'わからない', 'しらない', '知らなかった', '不明', '説明できない', '自信がない')


def digest(kind, payload):
    return hashlib.sha256(json.dumps([kind, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_events(root=ROOT):
    """不正ログを黙って無視しない。元ログの不足は改変せず呼出側へ返す。"""
    events = []
    ids, requests = set(), set()
    for path in sorted((pathlib.Path(root) / '演習ログ/記録').glob('*.md')):
        blocks = re.findall(r'^```json\s*\n(.*?)^```', path.read_text(encoding='utf-8'), re.S | re.M)
        if len(blocks) != 1:
            raise ValueError(f'JSONイベントが一意でない: {path}')
        e = json.loads(blocks[0])
        if e['id'] in ids or e['request_id'] in requests:
            raise ValueError(f'イベントID/要求ID重複: {path}')
        if digest(e['type'], e['payload']) != e['payload_sha256']:
            raise ValueError(f'ハッシュ不一致: {path}')
        ids.add(e['id']); requests.add(e['request_id'])
        events.append(e)
    return sorted(events, key=lambda e: (e['at'], e['sequence'], e['id']))


def date_of(at):
    value = dt.datetime.fromisoformat(at)
    if value.tzinfo is None:
        raise ValueError('日時にタイムゾーンが必要')
    return value.astimezone(JST).date()


def normalize(events, bank):
    """旧submit/checkpointの重複を統合。未回答と暫定代替を区別する。"""
    starts = {e['id']: e['payload'] for e in events if e['type'] == 'start'}
    versions = {(q['id'], q['version']): q for q in bank.values()}
    for e in events:
        if e['type'] == 'question_revision':
            for q in e['payload']['questions']:
                versions[(q['id'], q['version'])] = q
    rows, warnings = {}, []
    invalid = {(e['payload']['question_id'], e['payload']['version']) for e in events if e['type'] == 'question_report'}
    for e in events:
        p = e['payload']; sid = p.get('session_id', e['id'])
        if e['type'] not in ('submit', 'session_checkpoint', 'adaptive_attempt'):
            continue
        if e['type'] == 'adaptive_attempt':
            row = copy.deepcopy(p)
            q = row['question']; row.update(question_id=q['id'], version=q['version'], at=e['at'], evidence_event=e['id'])
            rows[(sid, q['id'])] = row
            continue
        if sid not in starts:
            warnings.append(f'{e["id"]}: start不在。登録問題からの照合は推定、代替の不足を補完しない')
        snapshots = {q['id']: q for q in starts.get(sid, {}).get('questions', [])}
        refs = {q['id']: q for q in p.get('question_refs', [])}
        for qid, answer in p.get('answers', {}).items():
            key = (sid, qid)
            ref_version = refs.get(qid, {}).get('version', 1)
            q = snapshots.get(qid) or versions.get((qid, ref_version))
            alt = next((p[k] for k in ('provisional_alternative', 'alternative_question') if p.get(k, {}).get('id') == qid), None)
            provisional = bool(alt or refs.get(qid, {}).get('provisional') or '@alternative' in qid or '-ALT' in qid)
            version = (q or {}).get('version', refs.get(qid, {}).get('version', 1))
            score = None
            if q and q['type'] != 'text' and (qid, version) not in invalid:
                score = int(sorted(answer) == sorted(q['answer']))
            if provisional:
                score = None  # 暫定結果は正式得点へ加算しない
            row = rows.setdefault(key, dict(session_id=sid, unit_id=p['unit_id'], question_id=qid, version=version))
            row.update(at=e['at'], answer=answer, score=score, provisional=provisional, evidence_event=e['id'])
            row['question'] = copy.deepcopy(q if not provisional else alt)
            row['snapshot_origin'] = 'start' if qid in snapshots else 'archive_lookup' if q else 'unavailable'
            if provisional:
                row['provisional_result'] = (p.get('objective_scores', {}).get(qid) if alt is None else alt.get('correct', alt.get('result')))
                row['original_question_id'] = (alt or {}).get('original_question_id', (alt or {}).get('replaces_for_display_only', re.sub(r'(-ALT\w+|@alternative)$', '', qid)))
            for field in ('review_need', 'review_necessity'):
                if qid in p.get(field, {}):
                    row['review_need'] = p[field][qid]
            if qid in p.get('review_need', {}) and qid in p.get('review_necessity', {}) and p['review_need'][qid] != p['review_necessity'][qid]:
                warnings.append(f'{e["id"]}: 復習必要性キー競合 {qid}')
                row['review_need'] = None
            if qid in p.get('free_responses', {}):
                row['free_response'] = p['free_responses'][qid]
            row['additional_questions'] = [a for a in p.get('additional_questions', []) if a.get('question_id') == qid] or row.get('additional_questions', [])
    for e in events:
        if e['type'] == 'learner_observation':
            p = e['payload']; key = (p['session_id'], p['question_id'])
            if key not in rows:
                warnings.append(f'{e["id"]}: 観測の対象回答が不在')
                continue
            row = rows[key]
            if row['version'] != p['version']:
                warnings.append(f'{e["id"]}: 観測の問題版不一致')
                continue
            row.setdefault('later_observations', []).append(copy.deepcopy(p))
            row.setdefault('interpretation', {}).update(p.get('interpretation', {}))
            # 回答日時と初回自由回答は保護。後日の観測だけで学習日を更新しない。
    for row in rows.values():
        row['void'] = (row['question_id'], row['version']) in invalid
        if row['void']:
            row['score'] = None
    return list(rows.values()), warnings


def concept_of(row):
    qid = row['question_id']; base = re.sub(r'(-ALT\w+|@alternative)$', '', qid)
    return BIO.get(base, (row['unit_id'] + '/' + base, (row.get('question') or {}).get('topic', base)))


def rebuild(events, bank):
    """事実を残し、AI解釈に代わる限定ルールの推定を明示する。"""
    rows, warnings = normalize(events, bank)
    concepts = {}
    for r in sorted(rows, key=lambda r: r['at']):
        if r['void']:
            continue
        targets = r.get('concept_ids') or [concept_of(r)[0]]
        for cid in targets:
            label = concept_of(r)[1] if cid == concept_of(r)[0] else cid
            c = concepts.setdefault(cid, dict(concept_id=cid, label=label, unit_id=r['unit_id'], observations=[], state='未学習', retention_confirmed=False))
            c['observations'].append(r)
            text = r.get('free_response', '')
            aq = ' '.join(a.get('summary', '') for a in r.get('additional_questions', []))
            explicit = r.get('interpretation', {})
            gap = any(s in text for s in GAPS) or explicit.get('knowledge_gap', False)
            c.update(last_study=date_of(r['at']).isoformat(), review_need=r.get('review_need'), depth='未確認', certainty='ルールによる推定', retention_confirmed=False)
            c['review_count'] = max(0, len(c['observations']) - 1)
            c['error_count'] = sum(a.get('score') == 0 for a in c['observations'])
            c['recurrence'] = c['error_count'] >= 2
            if r.get('score') == 0 or gap:
                c['state'] = '理解不足'; c['weak_candidate'] = True
            elif r.get('score') == 1:
                c['state'] = '学習中'; c['weak_candidate'] = False; c['depth'] = '用語認識'
            else:
                c['state'] = '未評価'; c['weak_candidate'] = gap
            # 要約中の本人理解は主観的申告であり、新たな設問での確認ではない。
            c['post_explanation_report'] = '理解した旨の申告' if '本人理解' in aq or explicit.get('post_explanation_understood') else None
            if c['post_explanation_report']:
                c['state'] = '定着確認待ち'
            if explicit.get('reason_verified') and r.get('score') == 1 and not gap:
                c['depth'] = explicit.get('depth', '仕組みの理解')
            # 旧第2問の自由回答は仕組み理解の候補。定着確定には使わない。
            if cid == 'BIO-0001/bilayer' and r.get('score') == 1 and '疎水性' in text:
                c['depth'] = '仕組みの理解（推定）'
            previous = c['observations'][:-1]
            delayed = any((date_of(r['at']) - date_of(a['at'])).days >= 7 and a.get('score') == 1 and a['question_id'] != r['question_id'] for a in previous)
            if delayed and r.get('score') == 1 and explicit.get('reason_verified') and not gap:
                c.update(state='定着確認済み', retention_confirmed=True, certainty='別問題・理由確認・7日以上の間隔')
            if cid == 'BIO-0001/protein' and ('セルロース' in text or 'セルロース' in aq):
                c['prerequisite_candidate'] = 'BIO-0001/biomolecule_classes'
    return dict(schema_version=2, concepts=concepts, attempts=rows, warnings=warnings,
                caveat='自由文全般の意味理解はChatGPTが根拠イベントとともに行う。本補助は限定的な抽出・ルール検証のみ。')


def plan(concept, today):
    """暫定間隔。自己評価5でも客観的弱点・定着未確認を打ち消さない。"""
    need = concept.get('review_need')
    if need is not None and (type(need) is not int or not 1 <= need <= 5):
        raise ValueError('復習必要性は1〜5または未取得')
    state = concept['state']
    days = 1 if state in ('理解不足', '未評価') else 3 if state == '定着確認待ち' else 21 if concept['retention_confirmed'] else 7
    if need in (1, 2, 3):
        days = min(days, {1: 1, 2: 2, 3: 3}[need])
    due = date_of(concept['last_study'] + 'T00:00:00+09:00') + dt.timedelta(days=days)
    if concept.get('prerequisite_candidate'):
        mode, target, level = '弱点補強', concept['prerequisite_candidate'], 1
    elif state == '理解不足':
        mode, target, level = '弱点補強', concept['concept_id'], 2
    elif state == '定着確認待ち' or due <= today:
        mode, target, level = '定着確認', concept['concept_id'], 3
    elif '仕組み' in concept['depth']:
        mode, target, level = '応用', concept['concept_id'] + '/application', 4
    else:
        mode, target, level = '基礎確認', concept['concept_id'], 2
    return dict(concept_id=concept['concept_id'], target=target, mode=mode, estimated_difficulty=level,
                next_review=due.isoformat(), due=due <= today, priority=0 if state == '理解不足' else 1 if state == '定着確認待ち' else 2,
                avoid_question_ids=[r['question_id'] for r in concept['observations']],
                reason=f'{state}・{concept["depth"]}。同一問題の再利用ではなく論点を変えて確認')


def decide(model, unit_id, today, sources_available=True):
    if not sources_available:
        return dict(status='source_unavailable', action='資料添付・確認可能な公式資料・別区画から選択。未確認出典を作らない')
    selected = [plan(c, today) for c in model['concepts'].values() if c['unit_id'] == unit_id]
    if not selected:
        return dict(status='ready', mode='基礎確認', target=unit_id + '/diagnostic', estimated_difficulty=1, reason='履歴なし。本文から前提知識の診断を1問作成')
    return dict(status='ready', **sorted(selected, key=lambda x: (x['priority'], not x['due'], x['next_review'], x['concept_id']))[0])


def validate_question(q):
    for key in ('id', 'unit_id', 'concept_ids', 'prompt', 'options', 'answer', 'explanation', 'option_explanations', 'sources', 'mode', 'selection_reason', 'validation', 'correction_history', 'origin'):
        if key not in q or (not q[key] and key != 'correction_history'):
            raise ValueError('問題必須項目: ' + key)
    if type(q.get('version')) is not int or q['version'] < 1:
        raise ValueError('版番号が不正')
    if type(q.get('estimated_difficulty')) is not int or not 1 <= q['estimated_difficulty'] <= 5:
        raise ValueError('推定難易度が不正')
    if q.get('type') not in ('single', 'multi') or len(q['options']) < 2:
        raise ValueError('選択式で出題')
    if q['mode'] not in ('基礎確認', '弱点補強', '応用', '国試実践', '定着確認'):
        raise ValueError('出題モードが不正')
    if not all(isinstance(x, str) and x.strip() for x in q['options']):
        raise ValueError('空の選択肢')
    ans = q['answer']
    if not isinstance(ans, list) or not ans or any(type(i) is not int or i < 0 or i >= len(q['options']) for i in ans) or len(set(ans)) != len(ans):
        raise ValueError('正答添字が不正')
    if q['type'] == 'single' and len(ans) != 1:
        raise ValueError('単一正答数不一致')
    if len(q['option_explanations']) != len(q['options']):
        raise ValueError('全選択肢の解説が必要')
    if q['validation']['status'] not in ('text_checked', 'human_checked'):
        raise ValueError('未検証問題は出題しない')
    if not all(s.get('status') in ('text_checked', 'visual_checked') and s.get('title') and (s.get('file') or s.get('url')) and s.get('locator') and s.get('checked_at') and s.get('supports') for s in q['sources']):
        raise ValueError('未確認出典は根拠に使えない')
    return True


def make_event(kind, payload, request_id, sequence, at):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id) or type(sequence) is not int or sequence < 1:
        raise ValueError('要求ID・連番不正')
    date_of(at)
    if kind == 'adaptive_question':
        validate_question(payload['question'])
    if kind == 'adaptive_attempt':
        validate_question(payload['question'])
        if 'answer' not in payload or 'score' not in payload:
            raise ValueError('実回答と評価が必要')
        q, a = payload['question'], payload['answer']
        if not isinstance(a, list) or not a or any(type(i) is not int or not 0 <= i < len(q['options']) for i in a) or len(set(a)) != len(a) or (q['type'] == 'single' and len(a) != 1):
            raise ValueError('実回答の添字が不正')
        if type(payload['score']) not in (int, float) or payload['score'] != int(sorted(a) == sorted(q['answer'])):
            raise ValueError('採点と実回答が不一致')
    return dict(id=str(uuid.uuid4()), at=at, sequence=sequence, type=kind, request_id=request_id,
                payload_sha256=digest(kind, payload), payload=copy.deepcopy(payload))


def append_for_test(directory, event):
    """隔離テスト専用。GitHubへの保存は学習記録.mdのCAS手順で行う。"""
    directory = pathlib.Path(directory)
    if not (directory / 'TEST_ONLY').is_file():
        raise ValueError('本番書込み禁止: TEST_ONLYが必要')
    if event['payload_sha256'] != digest(event['type'], event['payload']):
        raise ValueError('イベントハッシュ不一致')
    sequences = []
    for path in directory.glob('*.md'):
        old = json.loads(re.search(r'```json\n(.*?)\n```', path.read_text(), re.S).group(1))
        if old['request_id'] == event['request_id']:
            if old['payload_sha256'] != event['payload_sha256']:
                raise ValueError('要求ID衝突')
            return old
        sequences.append(old['sequence'])
    if event['sequence'] != max(sequences, default=0) + 1:
        raise ValueError('連番衝突・欠落')
    path = directory / f'{event["sequence"]:08d}-{event["id"]}.md'
    with path.open('x', encoding='utf-8') as f:
        f.write('# 検証専用イベント\n\n```json\n' + json.dumps(event, ensure_ascii=False, indent=2) + '\n```\n')
    return event


if __name__ == '__main__':
    bank = {q['id']: q for q in json.loads((ROOT / '問題データ/questions.json').read_text(encoding='utf-8'))}
    model = rebuild(load_events(), bank)
    print(json.dumps(model, ensure_ascii=False, indent=2))
