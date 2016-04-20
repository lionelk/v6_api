import math
import re
from functools import partial

from c2corg_api.search import create_search, search_documents, \
    get_text_query, SEARCH_LIMIT_DEFAULT, SEARCH_LIMIT_MAX
from elasticsearch_dsl.query import Range, Term, Terms, Bool


def build_query(url_params, doc_type):
    """Creates an ElasticSearch query from query parameters.
    """
    params = url_params.copy()

    search_term = params.pop('q', '').strip()
    limit = params.pop('limit', None)
    limit = min(
        SEARCH_LIMIT_DEFAULT if limit is None else limit,
        SEARCH_LIMIT_MAX)
    offset = params.pop('offset', 0)

    search = create_search(doc_type)
    if search_term:
        search = search.query(get_text_query(search_term))

    search_model = search_documents[doc_type]
    for param in params:
        filter = create_filter(param, params.get(param), search_model)
        if filter:
            search = search.filter(filter)

    search = search.\
        fields([]).\
        extra(from_=offset, size=limit)

    # TODO add bbox filter
    # TODO add order by

    return search


def create_filter(field_name, query_term, search_model):
    """Creates an ES filter for a search tuple (field_name, query_term), e.g.
    `('e', '1500,2500')` (which creates a range filter on the elevation field).
    """
    if field_name not in search_model.queryable_fields:
        return None
    if not query_term:
        return None

    field = search_model.queryable_fields.get(field_name)
    if hasattr(field, '_range') and field._range:
        return create_range_filter(field, query_term)
    elif hasattr(field, '_enum'):
        return create_term_filter(field, query_term)
    elif hasattr(field, '_is_bool') and field._is_bool:
        return create_boolean_filter(field, query_term)
    elif hasattr(field, '_is_id') and field._is_id:
        return create_id_filter(field, query_term)
    elif hasattr(field, '_date_range') and field._date_range:
        return create_date_range_filter(field, query_term)

    return None


def create_range_filter(field, query_term):
    """Creates an ElasticSearch range filter.

    E.g. the call `create_range_filter(elevation_field, '1500,2500') creates
    the following filter:
        {'range': {'elevation': {'gte': 1500, 'lte': 2500}}}
    """
    query_terms = query_term.split(',')
    range_values = list(map(parse_num, query_terms))

    n = len(range_values)
    range_from = range_values[0] if n > 0 else None
    range_to = range_values[1] if n > 1 else None

    if range_from is None and range_to is None:
        return None

    range_params = {}
    if range_from is not None and not math.isnan(range_from):
        range_params['gte'] = range_from
    if range_to is not None and not math.isnan(range_to):
        range_params['lte'] = range_to

    kwargs = {field._name: range_params}
    return Range(**kwargs)


def create_term_filter(field, query_term):
    """Creates an ElasticSearch term/terms filter for an enum field.
    """
    query_terms = query_term.split(',')
    term_values = list(
        map(partial(parse_enum_value, field._enum), query_terms))
    term_values = [t for t in term_values if t is not None]

    if not term_values:
        return None
    elif len(term_values) == 1:
        kwargs = {field._name: term_values[0]}
        return Term(**kwargs)
    else:
        kwargs = {field._name: term_values}
        return Terms(**kwargs)


def create_boolean_filter(field, query_term):
    """Creates an ElasticSearch term filter for a boolean field.
    """
    filter_value = None
    if query_term in ['true', 'True', '1']:
        filter_value = True
    elif query_term in ['false', 'False', '0']:
        filter_value = False

    if filter_value is None:
        return None
    else:
        kwargs = {field._name: filter_value}
        return Term(**kwargs)


def create_id_filter(field, query_term):
    """Creates an ElasticSearch term filter for a field, that contains
    document ids.
    """
    query_terms = query_term.split(',')
    term_values = list(map(parse_num, query_terms))
    term_values = [t for t in term_values if t is not None]

    if not term_values:
        return None
    elif len(term_values) == 1:
        kwargs = {field._name: term_values[0]}
        return Term(**kwargs)
    else:
        kwargs = {field._name: term_values}
        return Terms(**kwargs)


def create_date_range_filter(field, query_term):
    """Creates an ElasticSearch date-range filter.

    This filter type is currently only used for Outing.date_start/date_end.

    Valid query terms are:
        2016-01-01
        2016-01-01,2016-01-01
        2016-01-01,2016-01-03

    """
    query_terms = query_term.split(',')
    range_values = list(map(parse_date, query_terms))
    range_values = [t for t in range_values if t is not None]

    n = len(range_values)
    if n == 0:
        return None
    elif n == 1 or range_values[0] == range_values[1]:
        # single date
        kwargs_start = {field.field_date_start: {'lte': range_values[0]}}
        kwargs_end = {field.field_date_end: {'gte': range_values[0]}}
        return Bool(must=[
            Range(**kwargs_start),
            Range(**kwargs_end)
        ])
    else:
        # date range
        kwargs_start = {field.field_date_start: {'gt': range_values[1]}}
        kwargs_end = {field.field_date_end: {'lt': range_values[0]}}
        return Bool(must_not=Bool(should=[
            Range(**kwargs_start),
            Range(**kwargs_end)
        ]))


def parse_num(s):
    try:
        try:
            return int(s)
        except ValueError:
            return float(s)
    except ValueError:
        return None


def parse_enum_value(valid_values, s):
    if s in valid_values:
        return s
    else:
        return None

DATE_REGEX = re.compile('^(?:[0-9]{2})?[0-9]{2}-[0-3]?[0-9]-[0-3]?[0-9]$')


def parse_date(s):
    if DATE_REGEX.match(s):
        return s
    else:
        return None
