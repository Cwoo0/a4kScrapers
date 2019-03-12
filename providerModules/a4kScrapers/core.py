# -*- coding: utf-8 -*-

import traceback
import json

from third_party import source_utils
from utils import tools, beautifulSoup, encode, decode, now
from utils import safe_list_get, get_caller_name, get_all_relative_py_files, wait_threads, quote_plus, quote, DEV_MODE, DEV_MODE_ALL, CACHE_LOG
from common_types import namedtuple, SearchResult, UrlParts, Filter, HosterResult
from request import threading, Request, ConnectTimeoutError, ReadTimeout
from scrapers import re, NoResultsScraper, GenericTorrentScraper, GenericExtraQueryTorrentScraper, MultiUrlScraper
from urls import trackers, hosters
from cache import check_cache_result, get_cache, set_cache, get_config, set_config

def get_scraper(soup_filter, title_filter, info, request=None, search_request=None, use_thread_for_info=False, custom_filter=None, caller_name=None):
    if caller_name is None:
        caller_name = get_caller_name()

    if caller_name not in trackers and caller_name not in hosters:
        return NoResultsScraper()

    if request is None:
        request = Request()

    def search(url, query):
        if '=%s' in url.search:
            query = quote_plus(query)
        else:
            query = query.decode('utf-8')

        return request.get(url.base + url.search % query)

    if search_request is None:
        search_request = search

    if caller_name in trackers:
        scraper_urls = trackers[caller_name]
    elif caller_name in hosters:
        scraper_urls = hosters[caller_name]

    urls = list(map(lambda t: UrlParts(base=t['base'], search=t['search']), scraper_urls))
    if DEV_MODE_ALL:
        scrapers = []
        for url in urls:
            scraper = TorrentScraper(None, request, search_request, soup_filter, title_filter, info, use_thread_for_info, custom_filter, url=url)
            scrapers.append(scraper)

        return MultiUrlScraper(scrapers)

    return TorrentScraper(urls, request, search_request, soup_filter, title_filter, info, use_thread_for_info, custom_filter)

class DefaultSources(object):
    def __init__(self, module_name, request=None, single_query=False, search_request=None):
        self._caller_name = module_name.split('.')[-1:][0]
        self._request = request
        self._single_query = single_query
        self._search_request = search_request

    def _get_scraper(self, title, genericScraper=None, use_thread_for_info=False, custom_filter=None):
        if genericScraper is None:
            genericScraper = GenericTorrentScraper(title)

        soup_filter = getattr(self, 'soup_filter', None)
        if soup_filter is None:
            soup_filter = genericScraper.soup_filter

        title_filter = getattr(self, 'title_filter', None)
        if title_filter is None:
            title_filter = genericScraper.title_filter

        info = getattr(self, 'info', None)
        if info is None:
            info = genericScraper.info

        parse_magnet = getattr(self, 'parse_magnet', None)
        if parse_magnet is not None:
            genericScraper.parse_magnet = parse_magnet

        parse_size = getattr(self, 'parse_size', None)
        if parse_size is not None:
            genericScraper.parse_size = parse_size

        parse_seeds = getattr(self, 'parse_seeds', None)
        if parse_seeds is not None:
            genericScraper.parse_seeds = parse_seeds

        self.genericScraper = genericScraper
        self.scraper = get_scraper(soup_filter,
                                   title_filter,
                                   info,
                                   caller_name=self._caller_name,
                                   request=self._request,
                                   search_request=self._search_request,
                                   use_thread_for_info=use_thread_for_info,
                                   custom_filter=custom_filter)
        return self.scraper

    def movie(self, title, year):
        return self._get_scraper(title) \
                   .movie_query(title, year, caller_name=self._caller_name)

    def episode(self, simple_info, all_info):
        return self._get_scraper(simple_info['show_title']) \
                   .episode_query(simple_info,
                                  caller_name=self._caller_name,
                                  single_query=self._single_query)

class DefaultExtraQuerySources(DefaultSources):
    def __init__(self, module_name, single_query=False, search_request=None, request_timeout=None):
        super(DefaultExtraQuerySources, self).__init__(module_name,
                                                       request=Request(sequental=True, timeout=request_timeout),
                                                       single_query=single_query,
                                                       search_request=search_request)

    def _get_scraper(self, title, custom_filter=None):
        genericScraper = GenericExtraQueryTorrentScraper(title,
                                                         context=self,
                                                         request=self._request)
        return super(DefaultExtraQuerySources, self)._get_scraper(title,
                                                                  genericScraper=genericScraper,
                                                                  use_thread_for_info=True,
                                                                  custom_filter=custom_filter)

class DefaultHosterSources(DefaultSources):
    def movie(self, imdb, title, localtitle, aliases, year):
        self._get_scraper(title)
        self._request = self.scraper._request

        simple_info = {}
        simple_info['title'] = title
        simple_info['year'] = year
        return simple_info

    def tvshow(self, imdb, tvdb, tvshowtitle, localtvshowtitle, aliases, year):
        self._get_scraper(tvshowtitle)
        self._request = self.scraper._request

        simple_info = {}
        simple_info['show_title'] = tvshowtitle
        simple_info['year'] = year
        return simple_info

    def episode(self, simple_info, imdb, tvdb, title, premiered, season, episode):
        simple_info['episode_title'] = title
        simple_info['episode_number'] = episode
        simple_info['season_number'] = season
        simple_info['episode_number_xx'] = episode.zfill(2)
        simple_info['season_number_xx'] = season.zfill(2)
        return simple_info

    def resolve(self, url):
        return url

    def sources(self, simple_info, hostDict, hostprDict):
        supported_hosts = hostDict + hostprDict
        sources = []

        try:
            query_type = None
            if simple_info.get('title', None) is not None:
                query_type = 'movie'
                query = '%s %s' % (simple_info['title'], simple_info['year'])
            else:
                query_type = 'episode'
                query = '%s S%sE%s' % (simple_info['show_title'], simple_info['season_number_xx'], simple_info['episode_number_xx'])

            url = self._request.find_url(self.scraper._urls)
            hoster_results = self.search(url, query)

            for result in hoster_results:
                quality = source_utils.getQuality(result.title)

                for url in result.urls:
                    domain = re.findall(r"https?:\/\/(www\.)?(.*?)\/.*?", url)[0][1]

                    if domain not in supported_hosts:
                        continue
                    if any(x in url for x in ['.rar', '.zip', '.iso']):
                        continue

                    quality_from_url = source_utils.getQuality(url)
                    if quality_from_url != 'SD':
                        quality = quality_from_url

                    sources.append({
                        'source': domain,
                        'quality': quality,
                        'language': 'en',
                        'url': url,
                        'info': [],
                        'direct': False,
                        'debridonly': False
                    })

            sources.reverse()

            tools.log('a4kScrapers.%s.%s: %s' % (query_type, self._caller_name, len(sources)), 'notice')

            return sources
        except:
            return sources

    def search(self, hoster_url, query):
        return []

class TorrentScraper(object):
    def __init__(self, urls, request, search_request, soup_filter, title_filter, info, use_thread_for_info=False, custom_filter=None, url=None):
        self._results = []
        self._temp_results = []
        self._results_from_cache = []
        self._url = url
        self._urls = urls
        self._request = request
        self._search_request = search_request
        self._soup_filter = soup_filter
        self._title_filter = title_filter
        self._info = info
        self._use_thread_for_info = use_thread_for_info
        self._custom_filter = custom_filter

        filterMovieTitle = lambda t: source_utils.filterMovieTitle(t, self.title, self.year)
        self.filterMovieTitle = Filter(fn=filterMovieTitle, type='single')

        filterSingleEpisode = lambda t: source_utils.filterSingleEpisode(self.simple_info, t)
        self.filterSingleEpisode = Filter(fn=filterSingleEpisode, type='single')

        filterSingleSpecialEpisode = lambda t: source_utils.filterSingleSpecialEpisode(self.simple_info, t)
        self.filterSingleSpecialEpisode = Filter(fn=filterSingleSpecialEpisode, type='single')

        filterSeasonPack = lambda t: source_utils.filterSeasonPack(self.simple_info, t)
        self.filterSeasonPack = Filter(fn=filterSeasonPack, type='season')

        filterShowPack = lambda t: source_utils.filterShowPack(self.simple_info, t)
        self.filterShowPack = Filter(fn=filterShowPack, type='show')

    def _search_core(self, query):
        try:
            response = self._search_request(self._url, query)
            if self._soup_filter is None:
                search_results = response
            else:
                search_results = self._soup_filter(response)
        except AttributeError:
            return []
        except:
            traceback.print_exc()
            return []

        results = []
        for el in search_results:
            try:
                title = self._title_filter(el)
                results.append(SearchResult(el=el, title=title))
            except:
                continue

        return results

    def _info_core(self, el, torrent):
        try:
            result = self._info(el, self._url, torrent)
            if result is not None and result['magnet'].startswith('magnet:?'):
                if result['hash'] == '':
                    result['hash'] = re.findall(r'btih:(.*?)\&', result['magnet'])[0]
                self._temp_results.append(result)
        except:
            pass

    def _get(self, query, filters):
        results = self._search_core(query.encode('utf-8'))

        threads = []
        for result in results:
            el = result.el
            title = result.title
            for filter in filters:
                custom_filter = False
                packageType = filter.type
                if self._custom_filter is not None:
                    if self._custom_filter.fn(title):
                        custom_filter = True
                        packageType = self._custom_filter.type

                if custom_filter or filter.fn(title):
                    torrent = {}
                    torrent['scraper'] = self.caller_name
                    torrent['hash'] = ''
                    torrent['package'] = packageType
                    torrent['release_title'] = title
                    torrent['size'] = None
                    torrent['seeds'] = None

                    if self._use_thread_for_info:
                        if len(threads) >= 5:
                            break

                        threads.append(threading.Thread(target=self._info_core, args=(el, torrent)))
                        if DEV_MODE:
                            wait_threads(threads)
                            threads = []
                    else:
                        self._info_core(el, torrent)

                    if DEV_MODE and len(self._temp_results) > 0:
                        return

        wait_threads(threads)

    def _query_thread(self, query, filters):
        return threading.Thread(target=self._get, args=(query, filters))

    def _get_cache(self, query):
        cache_result = get_cache(self.caller_name, query)
        self._cache_result = cache_result
        if cache_result is None:
            return False

        if not check_cache_result(cache_result, self.caller_name):
            return False

        parsed_result = cache_result['parsed_result']
        self._results_from_cache = parsed_result['cached_results'][self.caller_name]

        use_cache_only = parsed_result.get('use_cache_only', False)
        if use_cache_only and CACHE_LOG:
            tools.log('cache_direct_result', 'notice')

        return use_cache_only

    def _set_cache(self, query):
        set_cache(self.caller_name, query, self._temp_results, self._cache_result)

    def _sanitize_and_get_status(self):
        self._results = self._temp_results + self._results_from_cache

        additional_info = ''
        missing_size = 0
        missing_seeds = 0

        for torrent in self._results:
            if torrent['size'] is None:
                missing_size += 1
                if not DEV_MODE:
                    torrent['size'] = 0
            if torrent['seeds'] is None:
                missing_seeds += 1
                if not DEV_MODE:
                    torrent['seeds'] = 0

        if missing_size > 0:
            additional_info += ', %s missing size info' % missing_size

        if missing_seeds > 0:
            additional_info += ', %s missing seeds info' % missing_seeds

        results = {}
        for result in self._results:
            item_key = result['hash']
            item = results.get(result['hash'], None)
            if item is None:
                results[item_key] = result
                continue
            if item['size'] == 0 and result['size'] > 0:
                item['size'] = result['size']
            if item['seeds'] == 0 and result['seeds'] > 0:
                item['seeds'] = result['seeds']

        self._results = list(results.values())
        stats = str(len(self._results))

        if self.caller_name != 'showrss':
            stats += additional_info

        return stats

    def _get_movie_results(self):
        tools.log('a4kScrapers.movie.%s: %s' % (self.caller_name, self._sanitize_and_get_status()), 'notice')
        return self._results

    def _get_episode_results(self):
        tools.log('a4kScrapers.episode.%s: %s' % (self.caller_name, self._sanitize_and_get_status()), 'notice')
        return self._results

    def _episode(self, query):
        return self._query_thread(query, [self.filterSingleEpisode])

    def _episode_special(self, query):
        return self._query_thread(query, [self.filterSingleSpecialEpisode])

    def _season(self, query):
        return self._query_thread(query, [self.filterSeasonPack])

    def _pack(self, query):
        return self._query_thread(query, [self.filterShowPack])

    def _season_and_pack(self, query):
        return self._query_thread(query, [self.filterSeasonPack, self.filterShowPack])

    def movie_query(self, title, year, caller_name=None):
        if caller_name is None:
            caller_name = get_caller_name()

        self.caller_name = caller_name

        self.title = title
        self.year = year

        full_query = '%s %s' % (self.title, self.year)
        use_cache_only = self._get_cache(full_query)
        if use_cache_only:
            return self._get_movie_results()

        try:
            if self._url is None:
                self._url = self._request.find_url(self._urls)
                if self._url is None:
                    self._set_cache(full_query)
                    return self._get_movie_results()

            movie = lambda query: self._query_thread(query, [self.filterMovieTitle])
            wait_threads([movie(title + ' ' + year)])

            if len(self._temp_results) == 0:
                wait_threads([movie(title)])

            self._set_cache(full_query)
            return self._get_movie_results()

        except:
            self._set_cache(full_query)
            return self._get_movie_results()

    def episode_query(self, simple_info, auto_query=True, single_query=False, caller_name=None):
        if caller_name is None:
            caller_name = get_caller_name()

        self.caller_name = caller_name

        if '.' in simple_info['show_title']:
            no_dot_show_title = simple_info['show_title'].replace('.', '')
            simple_info['show_aliases'].append(source_utils.cleanTitle(no_dot_show_title))
            simple_info['show_aliases'] = list(set(simple_info['show_aliases']))
            simple_info['show_title'] = no_dot_show_title

        self.simple_info = simple_info
        self.year = simple_info['year']
        self.country = simple_info['country']
        self.show_title = source_utils.cleanTitle(simple_info['show_title'])
        self.episode_title = source_utils.cleanTitle(simple_info['episode_title'])
        self.season_x = simple_info['season_number']
        self.episode_x = simple_info['episode_number']
        self.season_xx = self.season_x.zfill(2)
        self.episode_xx = self.episode_x.zfill(2)

        full_query = '%s %s %s %s %s' % (self.show_title, self.year, self.season_xx, self.episode_xx, self.episode_title)
        use_cache_only = self._get_cache(full_query)
        if use_cache_only:
            return self._get_episode_results()

        try:
            if self._url is None:
                self._url = self._request.find_url(self._urls)
                if self._url is None:
                    self._set_cache(full_query)
                    return self._get_episode_results()

            if auto_query is False:
                wait_threads([self._episode('')])
                self._set_cache(full_query)
                return self._get_episode_results()

            # specials
            if self.season_x == '0':
                wait_threads([self._episode_special(self.show_title + ' %s' % self.episode_title)])
                self._set_cache(full_query)
                return self._get_episode_results()

            wait_threads([
                self._episode(self.show_title + ' S%sE%s' % (self.season_xx, self.episode_xx))
            ])

            if single_query or DEV_MODE:
                self._set_cache(full_query)
                return self._get_episode_results()

            queries = [
                self._season(self.show_title + ' Season ' + self.season_x),
                self._season(self.show_title + ' S%s' % self.season_xx),
                self._pack(self.show_title + ' Seasons'),
                self._season_and_pack(self.show_title + ' Complete')
            ]

            if self._use_thread_for_info:
                wait_threads([queries[0]])
            else:
                wait_threads(queries)

            self._set_cache(full_query)
            return self._get_episode_results()

        except:
            self._set_cache(full_query)
            return self._get_episode_results()
