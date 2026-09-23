"""Run inside Bazarr container. Raw provider API only; no app imports or saves.

Input JSON: action=search, season, episode, imdb; or action=download,file_id.
Output JSON: provider metadata or base64 raw bytes. Credentials never emitted.
"""
import ast
import os
import base64
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request
import re
import io
import urllib.error

TOKEN=None
HOST='api.opensubtitles.com'
STAGE='initialization'
RESPONSE_HEADERS={}

def diagnostic_headers(headers):
    return {k:v for k,v in headers.items() if k.lower() in
            ('retry-after','ratelimit-reset','ratelimit-limit','ratelimit-remaining',
             'content-type','server','date') or k.lower().startswith('x-ratelimit-')}

def authenticated_download(call,headers,config,file_id):
    """Refresh this helper's private token once on a rejected download request.

    Never retry quota/429/server errors or signed-content failures here. No token
    is written to Bazarr or disk; login requests must not carry a stale bearer.
    """
    global TOKEN,HOST
    for attempt in range(2):
        if TOKEN is None:
            headers.pop('Authorization',None)
            login=call('api.opensubtitles.com','login',dict(username=config['username'],password=config['password']))
            TOKEN=login['token']
            HOST=login.get('base_url','api.opensubtitles.com')
        headers['Authorization']='Bearer '+TOKEN
        try:
            return call(HOST,'download',{'file_id':int(file_id),'sub_format':'srt'})
        except urllib.error.HTTPError as error:
            if error.code!=401:raise
            TOKEN=None
            HOST='api.opensubtitles.com'
            headers.pop('Authorization',None)
            if attempt:raise
            error.close()

sys.path.insert(0,'/app/bazarr/bin/libs')
import yaml
import requests
SESSION=requests.Session()

def main(args):
    global TOKEN,HOST,STAGE
    api_key=os.environ.get('OPENSUBTITLES_API_KEY')
    if api_key:
        config=dict(username=os.environ['OPENSUBTITLES_USERNAME'],password=os.environ['OPENSUBTITLES_PASSWORD'])
    else:
        config=yaml.safe_load(Path('/config/config/config.yaml').read_text())['opensubtitlescom']
        tree=ast.parse(Path('/app/bazarr/bin/bazarr/app/get_providers.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Dict):
                keys=[k.value if isinstance(k,ast.Constant) else None for k in node.keys]
                if 'use_hash' in keys and 'include_ai_translated' in keys and 'api_key' in keys:
                    api_key=ast.literal_eval(node.values[keys.index('api_key')])
    if not api_key:
        raise ValueError('Provider application key not found')
    headers={'Api-Key':api_key,'User-Agent':'Bazarr','Content-Type':'application/json','Accept':'application/json'}
    def call(host,path,payload=None):
        global STAGE,RESPONSE_HEADERS
        STAGE=path.split('?')[0]
        if host!='api.opensubtitles.com' and not host.endswith('.opensubtitles.com'):
            raise ValueError('Unexpected provider API host')
        url='https://'+host+'/api/v1/'+path
        r=SESSION.request('POST' if payload is not None else 'GET',url,json=payload,
                          headers=headers,timeout=45,allow_redirects=False)
        for _ in range(3):
            if r.status_code not in (301,302,307,308) or payload is not None:break
            target=urllib.parse.urljoin(url,r.headers.get('Location',''))
            parsed=urllib.parse.urlparse(target)
            if parsed.scheme!='https' or parsed.hostname!=host or parsed.path!='/api/v1/subtitles':break
            url=target
            r=SESSION.get(url,headers=headers,timeout=45,allow_redirects=False)
        RESPONSE_HEADERS[STAGE]=diagnostic_headers(r.headers)
        if r.status_code>=300:
            raise urllib.error.HTTPError(url,r.status_code,'Provider error',r.headers,io.BytesIO(r.content))
        return r.json()
    if args['action']=='search':
        params=dict(languages='en',ai_translated='exclude')
        if args.get('page',1)>1:params['page']=args['page']
        if args.get('season') is not None:
            params.update(season_number=args['season'],episode_number=args['episode'],
                          parent_imdb_id=args['imdb'].removeprefix('tt'))
        else:params.update(imdb_id=args['imdb'].removeprefix('tt'))
        result=call('api.opensubtitles.com','subtitles?'+urllib.parse.urlencode(sorted(params.items())))
        print(json.dumps(result))
    elif args['action']=='download':
        result=authenticated_download(call,headers,config,args['file_id'])
        url=result['link']
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='https':
            raise ValueError('Non-HTTPS download rejected')
        # Signed content URL does not receive API credentials.
        STAGE='content'
        r=SESSION.get(url,headers={'User-Agent':'Bazarr','Accept':'*/*'},timeout=45)
        RESPONSE_HEADERS[STAGE]=diagnostic_headers(r.headers)
        if r.status_code>=300:
            raise urllib.error.HTTPError('content',r.status_code,'Content error',r.headers,io.BytesIO(r.content))
        content=r.content
        if len(content)>2000000 or b'-->' not in content:
            raise ValueError('Invalid or oversized SRT')
        print(json.dumps({'content':base64.b64encode(content).decode(),
                          'remaining':result.get('remaining'),'headers':RESPONSE_HEADERS}))
    else:
        raise ValueError('Unsupported action')

if __name__=='__main__':
    for line in sys.stdin:
        try:
            main(json.loads(line))
        except Exception as e:
            body=''
            if hasattr(e,'read'):
                raw=e.read(16000).decode('utf-8',errors='replace')
                # Only generic diagnostic text; never echo tokens/URLs/forms.
                titles=re.findall(r'<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>',raw,re.S|re.I)
                body=' '.join(re.sub('<[^>]+>',' ',x) for x in titles)[:300]
                if not titles:
                    try:body=str(json.loads(raw).get('message',''))[:300]
                    except ValueError:body='non-JSON response without title'
                body=re.sub(r'https?://\S+','[URL]',body)
                body=re.sub(r'[A-Za-z0-9_./+=-]{40,}','[REDACTED]',body)
            # No response bodies, credentials or signed URLs in logs.
            print(json.dumps({'error':type(e).__name__,'status':getattr(e,'code',None),
                              'stage':STAGE,'retry_after':getattr(e,'headers',{}).get('Retry-After'),
                              'headers':diagnostic_headers(getattr(e,'headers',{})),
                              'message':body,
                              'previous_headers':RESPONSE_HEADERS}))
        sys.stdout.flush()
