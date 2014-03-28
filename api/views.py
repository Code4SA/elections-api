from api import app, logger, db
from flask import jsonify, request, make_response, render_template, redirect, current_app
from models import *
from serializers import serialize_area
import json
import time
from datetime import timedelta 
from functools import update_wrapper
from sqlalchemy.sql import func

HOST = app.config['HOST']

event_types = ["provincial", "national"]
years = [1999, 2004, 2009]
areas = ["province", "municipality", "ward", "voting_district"]

def crossdomain(origin=None, methods=None, headers=None,
                max_age=21600, attach_to_all=True,
                automatic_options=True):
    if methods is not None:
        methods = ', '.join(sorted(x.upper() for x in methods))
    if headers is not None and not isinstance(headers, basestring):
        headers = ', '.join(x.upper() for x in headers)
    if not isinstance(origin, basestring):
        origin = ', '.join(origin)
    if isinstance(max_age, timedelta):
        max_age = max_age.total_seconds()

    def get_methods():
        if methods is not None:
            return methods

        options_resp = current_app.make_default_options_response()
        return options_resp.headers['allow']

    def decorator(f):
        def wrapped_function(*args, **kwargs):
            if automatic_options and request.method == 'OPTIONS':
                resp = current_app.make_default_options_response()
            else:
                resp = make_response(f(*args, **kwargs))
            if not attach_to_all and request.method != 'OPTIONS':
                return resp

            h = resp.headers

            h['Access-Control-Allow-Origin'] = origin
            h['Access-Control-Allow-Methods'] = get_methods()
            h['Access-Control-Max-Age'] = str(max_age)
            if headers is not None:
                h['Access-Control-Allow-Headers'] = headers
            return resp

        f.provide_automatic_options = False
        return update_wrapper(wrapped_function, f)
    return decorator

class ApiException(Exception):
    """
    Class for handling all of our expected API errors.
    """

    def __init__(self, status_code, message):
        Exception.__init__(self)
        self.message = message
        self.status_code = status_code

    def to_dict(self):
        rv = {
            "code": self.status_code,
            "message": self.message
        }
        return rv

@app.errorhandler(ApiException)
def handle_api_exception(error):
    """
    Error handler, used by flask to pass the error on to the user, rather than catching it and throwing a HTTP 500.
    """

    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


def validate_event_type(event_type):

    event_type = event_type.lower()
    tmp = ", ".join(event_types)
    if not event_type in event_types:
        raise ApiException(422, "Incorrect event_type specified. Please use one of: " + tmp + ".")
    return event_type


def validate_year(year):

    tmp = ", ".join(str(x) for x in years)
    try:
        year = int(year)
    except ValueError as e:
        raise ApiException(422, "Incorrect year specified. Please use one of: " + tmp + ".")
    if not year in years:
        raise ApiException(422, "Incorrect year specified. Please use one of: " + tmp + ".")
    return year


def validate_area(area):

    area = area.lower()
    tmp = ", ".join(areas)
    if not area in areas:
        raise ApiException(422, "Incorrect area specified. Please use one of: " + tmp + ".")
    return area


@app.route('/')
@crossdomain(origin='*')
def index_event_types():
    """
    Landing page. Return links to available event_types.
    """

    out = {}
    for event_type in event_types:
        out[event_type] = request.url_root + event_type + "/"
    return jsonify(out)


@app.route('/<event_type>/')
@crossdomain(origin='*')
def index_years(event_type):
    """
    Return links to available years.
    """

    event_type = validate_event_type(event_type)
    out = {}
    for year in years:
        out[year] = request.url_root + event_type + "/" + str(year) + "/"
    return jsonify(out)


@app.route('/<event_type>/<year>/')
@crossdomain(origin='*')
def results_overall(event_type, year):
    """
    Return overall national results, with links to available areas.
    """

    event_type = validate_event_type(event_type)
    year = validate_year(year)
    out = {}

    item = Country.query.filter(Country.year==year).first()
    out['results'] = serialize_area(item, event_type)['results']

    for area in areas:
        if area != 'ward' or year >= 2009:
            out[area] = request.url_root + event_type + "/" + str(year) + "/" + area + "/"
    return jsonify(out)


@app.route('/<event_type>/<year>/<area>/')
@app.route('/<event_type>/<year>/<area>/<area_id>/')
@crossdomain(origin='*')
def results_by_area(event_type, year, area, area_id=None):
    """
    Return results for the specified area, with links to available parent areas where applicable.
    """

    # validate endpoints
    event_type = validate_event_type(event_type)
    year = validate_year(year)
    area = validate_area(area)

    # validate filter parameters
    filter_area = None
    filter_id = None
    for tmp_area in reversed(areas):
        if request.args.get(tmp_area):
            filter_area = tmp_area
            filter_id = request.args.get(tmp_area)

            # throw an exception, if this is not a viable filter for the specified area
            if areas.index(filter_area) >= areas.index(area):
                raise ApiException(422, "The specified filter parameter cannot be used in this query.")
            break

    # validate paging parameters
    page = 0
    per_page = 50
    if request.args.get('page'):
        try:
            page = int(request.args.get('page'))
        except ValueError:
            raise ApiException(422, "Please specify a valid 'page'.")

    if request.args.get('per_page'):
        try:
            per_page = int(request.args.get('per_page'))
        except ValueError:
            raise ApiException(422, "Please specify a valid 'per_page'.")

    all_results = False

    if request.args.get('all_results'):
        all_results = True

    models = {
        "province": (Province, Province.province_id),
        "municipality": (Municipality, Municipality.municipality_id),
        "ward": (Ward, Ward.ward_id),
        "voting_district": (VotingDistrict, VotingDistrict.voting_district_id)
    }

    model_filters = {
        "municipality": {
            "province": Municipality.province,
        },
        "ward": {
            "province": Ward.province,
            "municipality": Ward.municipality,
        },
        "voting_district": {
            "province": VotingDistrict.province,
            "municipality": VotingDistrict.municipality,
            "ward": VotingDistrict.ward,
        },
    }

    if area_id:
        out = models[area][0].query.filter(models[area][1] == area_id).first()
        out = serialize_area(out, event_type)
    else:
        if filter_area and filter_id:
            logger.debug("filtering: " + filter_area + " - " + filter_id)
            # retrieve the entity that will be filtered on
            obj = models[filter_area][0].query.filter(models[filter_area][1]==filter_id).first()
            if obj is None:
                raise ApiException(404, "Could not find the specified filter. Check that you have provided a valid ID, or remove the filter.")
            count = models[area][0].query.filter(models[area][0].year==year).filter(model_filters[area][filter_area]==obj).count()
            if (all_results):
                items = models[area][0].query.filter(models[area][0].year==year).filter(model_filters[area][filter_area]==obj).order_by(models[area][1]).all()
            else:
                items = models[area][0].query.filter(models[area][0].year==year).filter(model_filters[area][filter_area]==obj).order_by(models[area][1]).limit(per_page).offset(page*per_page).all()
        else:
            count = models[area][0].query.filter(models[area][0].year==year).count()
            if (all_results):
                items = models[area][0].query.filter(models[area][0].year==year).order_by(models[area][1]).all()
            else:
                items = models[area][0].query.filter(models[area][0].year==year).order_by(models[area][1]).limit(per_page).offset(page*per_page).all()
        next = None
        if count > (page + 1) * per_page:
            next = request.url_root + event_type + "/" + str(year) + "/" + area + "/?page=" + str(page+1)
        results = []
        for item in items:
            results.append(serialize_area(item, event_type))
        if len(results) == 0:
            raise ApiException(404, "Not Found")
        out = {
            'count': count,
            'next': next,
            'results': results
        }
    return jsonify(out)