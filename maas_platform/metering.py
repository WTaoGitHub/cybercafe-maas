"""Final successful OCR facts; pricing and token estimation are out of scope."""
from datetime import datetime, timezone
from psycopg.types.json import Jsonb


def tokens_from_response(envelope):
    value=envelope.get('usage') if isinstance(envelope,dict) else None
    if value is None:
        return {'status':'unavailable','input_tokens':None,'output_tokens':None,'source':None,'reason':'UPSTREAM_USAGE_ABSENT'}
    if isinstance(value,dict):
        a=value.get('prompt_tokens');b=value.get('completion_tokens');total=value.get('total_tokens')
        valid=all(type(n) is int and 0<=n<=2**63-1 for n in (a,b))
        if valid and ('total_tokens' not in value or (type(total) is int and total==a+b)):
            return {'status':'available','input_tokens':a,'output_tokens':b,'source':'upstream_reported','reason':None}
    return {'status':'unavailable','input_tokens':None,'output_tokens':None,'source':None,'reason':'UPSTREAM_USAGE_INVALID'}


def receipt(source_type, source_id, model, revision, tokens=None):
    return {'id':'usage_'+source_type+'_'+source_id,'status':'final','source_type':source_type,
            'source_id':source_id,'model':model,'revision':revision,'successful_images':1,
            'tokens':tokens or tokens_from_response(None),
            'recorded_at':datetime.now(timezone.utc).isoformat()}


def persist(conn, project, value):
    conn.execute('INSERT INTO ocr_usage_records(id,project,source_type,source_id,receipt) VALUES(%s,%s,%s,%s,%s)',
                 (value['id'],project,value['source_type'],value['source_id'],Jsonb(value)))


def single_image_ocr(body, envelope):
    """Only the supported single-image Chat shape can yield new image facts.

    Legacy Chat passthrough may return 200 for other upstream JSON; preserve its
    body/status but do not turn an unverified result into a billable image.
    """
    import json
    try:
        images=[part for message in body['messages'] if isinstance(message.get('content'),list)
                for part in message['content'] if part.get('type')=='image_url']
        if len(images)!=1 or not images[0]['image_url']['url'].startswith(('data:image/png;base64,','data:image/jpeg;base64,')):return False
        result=json.loads(envelope['choices'][0]['message']['content'])
        return isinstance(result['lines'],list) and all(isinstance(line,dict) and isinstance(line.get('text'),str) for line in result['lines'])
    except (KeyError,TypeError,ValueError,IndexError,AttributeError):return False
