from pathlib import Path
import re
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]

def test_added_toggle_requests_newest_resets_page_and_renders_dates():
    html=(ROOT/'webapp/static/index.html').read_text()
    html=re.sub(r'<script\b[^>]*>.*?</script>','',html,flags=re.S)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page()
        page.route('**/*',lambda route:route.abort())
        page.set_content(html,wait_until='domcontentloaded')
        page.evaluate('''() => {
            window.requests=[];
            window.fetch=async url => {
                window.requests.push(String(url));
                const data={total:81,channels:[{channel_name:'Presenter Test',channel_url:'https://youtube.com/channel/test',
                    first_seen_at:Date.now()/1000-7200,avatar_confidence:'likely',recent_videos:[]}]};
                return {ok:true,status:200,json:async()=>data,text:async()=>JSON.stringify(data)};
            };
        }''')
        page.add_script_tag(path=str(ROOT/'webapp/static/app.js'))
        page.evaluate("_nfPage=3;toggleNicheFilter('new')")
        page.wait_for_function('window.requests.length>0')
        assert 'sort=newest' in page.evaluate('requests.at(-1)')
        assert 'added_within_days=7' in page.evaluate('requests.at(-1)')
        assert 'offset=0' in page.evaluate('requests.at(-1)')
        assert page.locator('#nf-toggle-new').get_attribute('aria-pressed')=='true'
        assert 'Added in the past 7 days' in page.locator('#nf-filter-chips').inner_text()
        assert 'Added 2 hours ago' in page.locator('#nf-results').inner_text()
        assert 'AI presenter candidate' in page.locator('#nf-results').inner_text()
        assert page.locator('#nf-results time').get_attribute('datetime')
        assert page.locator('#nf-results time').get_attribute('title')
        page.evaluate('nicheFinderNextPage()')
        page.wait_for_function("requests.at(-1).includes('offset=40')")
        page.evaluate("removeNicheFilterChip('new')")
        page.wait_for_function("!requests.at(-1).includes('added_within_days')")
        assert page.locator('#nf-toggle-new').get_attribute('aria-pressed')=='false'
        page.evaluate("toggleNicheFilter('new');clearNicheFilters()")
        assert not page.locator('#nf-f-new').is_checked()
        assert page.locator('#nf-toggle-new').get_attribute('aria-pressed')=='false'
        assert page.evaluate("_nfAddedTime(null)") is None
        assert page.evaluate("_nfAddedTime(1791200000,1791200030000).relative")=='just now'
        browser.close()
