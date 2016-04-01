from collections import defaultdict
import math

from models.country import country_info
from models.country import get_name_from_iso
from models.country import pacific_rim_east, pacific_rim_west
from models.source import sources_metadata
from models.scientist_stars import scientists_twitter


from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict

from app import db
from util import date_as_iso_utc

import datetime
import shortuuid

def get_badge_assigner(name):
    for assigner in all_badge_assigners():
        if assigner.__name__ == name:
            return assigner
    return None


def all_badge_assigners():

    # temporarily just run a few
    # assigners = []
    # for assigner in BadgeAssigner.__subclasses__():
    #     if assigner.__name__ in ["hot_streak", "global_reach", "impressions", "babel"]:
    #         assigners.append(assigner)
    #end temporary.  add next line back in

    assigners = BadgeAssigner.__subclasses__()

    assigners.sort(key=lambda x: x.group)
    return assigners

def badge_configs_without_functions():
    configs = {}
    for assigner in all_badge_assigners():
        configs[assigner.__name__] = assigner.config_dict()
    return configs


class Badge(db.Model):
    id = db.Column(db.Text, primary_key=True)
    name = db.Column(db.Text)
    orcid_id = db.Column(db.Text, db.ForeignKey('person.orcid_id'))
    created = db.Column(db.DateTime)
    level = db.Column(db.Float)
    value = db.Column(db.Float)
    percentile = db.Column(db.Float)
    support = db.Column(db.Text)
    products = db.Column(MutableDict.as_mutable(JSONB))


    def __init__(self, assigned=True, **kwargs):
        self.id = shortuuid.uuid()[0:10]
        self.created = datetime.datetime.utcnow().isoformat()
        self.assigned = assigned
        self.products = {}
        super(Badge, self).__init__(**kwargs)

    @property
    def dois(self):
        if self.products:
            return self.products.keys()
        return []

    @property
    def num_products(self):
        if self.products:
            return len(self.products)
        else:
            return 0

    def add_product(self, my_product):
        self.products[my_product.doi] = True

    def add_products(self, products_list):
        for my_product in products_list:
            self.add_product(my_product)

    def remove_all_products(self):
        self.products = {}

    @property
    def my_badge_type(self):
        assigner = get_badge_assigner(self.name)
        if assigner:
            my_assigner = assigner()
        else:
            my_assigner = dummy_badge_assigner()
        return my_assigner

    @property
    def threshold(self):
        return self.my_badge_type.get_threshold(self.level)

    @property
    def sort_score(self):

        sort_score = self.percentile * self.my_badge_type.importance

        if self.my_badge_type.group == "fun":
            sort_score -= 1
        return sort_score

    @property
    def description(self):
        description_string = self.my_badge_type.description
        if "{value}" in description_string:
            description_string = description_string.format(value=round(self.value, 1))

        if self.percentile > 0.5:
            description_string = u"{} Congrats, that's more than {}% of researchers!".format(
                description_string, int(math.floor(100*self.percentile)))
        return description_string

    @property
    def display_max_level(self):
        return math.ceil(self.my_badge_type.max_level/2.0)

    @property
    def display_level(self):
        return math.ceil(self.level/2.0)

    def set_percentile(self, refset_list):
        self.percentile = self._calc_percentile(refset_list, self.value)
        print u"set percentile for {} {} to {}".format(self.name, self.value, self.percentile)

    def _calc_percentile(self, refset, value):
        if value is None:  # distinguish between that and zero
            return None

        try:
            matching_index = refset.index(value)
            percentile = float(matching_index) / len(refset)
        except ValueError:
            # not in index.  maybe has no impact because no academic contributions
            print u"not setting percentile for {}; looks like not academic".format(self.name)
            percentile = None
        return percentile


    def __repr__(self):
        return u'<Badge {id} {name} ({value})>'.format(
            id=self.id,
            name=self.name,
            value=self.value
        )

    def to_dict(self):
        if self.products:
            product_list = self.products.keys()

        resp =  {
            "id": self.id,
            "name": self.name,
            "created": date_as_iso_utc(self.created),
            "num_products": self.num_products,
            "support": self.support,
            "level": self.display_level,
            "value": self.value,
            "percentile": self.percentile,
            "sort_score": self.sort_score,
            "description": self.description,
            "extra_description": self.my_badge_type.extra_description,
            "group": self.my_badge_type.group,
            "display_name": self.my_badge_type.display_name,
            "max_level": self.display_max_level,
            "is_for_products": self.my_badge_type.is_for_products
        }
        return resp


class BadgeLevel(object):
    def __init__(self, level, threshold=None, custom_description=None):
        self.level = level
        self.threshold = threshold
        self.custom_description = custom_description

    def __repr__(self):
        return u'<BadgeLevel level={level} (threshold={threshold})>'.format(
            level=self.level,
            threshold=self.threshold
        )


class BadgeAssigner(object):
    display_name = ""
    level = 1
    is_for_products = True
    group = None
    description = ""
    extra_description = None
    img_url = None
    video_url = None
    credit = None
    next_level = None
    levels = []
    threshold = None
    value = None
    is_valid_badge = True
    importance = 1

    def __init__(self):
        self.candidate_badge = Badge(name=self.__class__.__name__)
        self.assigned = False

    def get_threshold(self, level):
        for my_level in self.levels:
            if my_level.level == level:
                return my_level.threshold
        return None

    @property
    def name(self):
        return self.__class__.__name__

    @property
    def max_level(self):
        if not self.levels:
            return 1  # is a single level badge

        ordered_levels_reversed = sorted(self.levels, key=lambda x: x.level, reverse=True)
        resp = ordered_levels_reversed[0].level
        return resp

    # override this in subclasses
    def decide_if_assigned(self, person):
        return None

    def decide_if_assigned_with_levels(self, person):
        ordered_levels_reversed = sorted(self.levels, key=lambda x: x.level, reverse=True)
        for my_level in ordered_levels_reversed:
            self.decide_if_assigned_threshold(person, my_level.threshold)
            if self.assigned:
                self.level = my_level.level
                self.threshold = my_level.threshold
                return
        return None

    def get_badge_or_None(self, person):
        if self.levels:
            self.decide_if_assigned_with_levels(person)
        else:
            self.decide_if_assigned(person)

        if self.assigned:
            self.candidate_badge.level = self.level
            return self.candidate_badge
        return None

    @classmethod
    def config_dict(cls):
        resp = {
            "name": cls.__name__,
            "display_name": cls.display_name,
            "is_for_products": cls.is_for_products,
            "group": cls.group,
            "description": cls.description,
        }
        return resp



# with nulls removed

# 0.18204108320165599	1
# 0.373344957227701002	2
# 0.434860785702609998	3
# 0.698632376178281	4
# 0.878112570152019045	5
# 0.938375197515393	6
# 0.965836647959462002	7
# 0.98910259903013098	8
# 0.993298098403531005	9
# 0.994660273524764049	10

# for use when other things have been deleted
class dummy_badge_assigner(BadgeAssigner):
    is_valid_badge = False

class depsy(BadgeAssigner):
    display_name = "Software reuse"
    is_for_products = False
    group = "channels"
    description = u"Your software impact is in the top {value} percent of all research software creators on Depsy"
    importance = .8
    levels = [
        BadgeLevel(1, threshold=0.01),
        # BadgeLevel(2, threshold=0.25),
        # BadgeLevel(3, threshold=0.5),
        # BadgeLevel(4, threshold=0.75),
        # BadgeLevel(5, threshold=0.9)
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        if person.depsy_percentile:
            if person.depsy_percentile > threshold:
                self.assigned = True
                self.candidate_badge.value = person.depsy_percentile
                self.candidate_badge.support = u"You are in the {} percentile <a href='http://depsy.org/person/{}'>on Depsy</a>.".format(
                    round(person.depsy_percentile * 100, 0),
                    person.depsy_id
                )



class big_hit(BadgeAssigner):
    display_name = "Big Hit"
    is_for_products = True
    group = "reach"
    description = u"You have a product with an Altmetric.com score of more than {value}."
    importance = .9
    levels = [
        BadgeLevel(1, threshold=3),
        # BadgeLevel(2, threshold=25),
        # BadgeLevel(3, threshold=50),
        # BadgeLevel(4, threshold=100),
        # BadgeLevel(5, threshold=250)
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        self.candidate_badge.value = 0
        for my_product in person.products:
            if my_product.altmetric_score > self.candidate_badge.value:
                self.assigned = True
                self.candidate_badge.value = my_product.altmetric_score
                self.candidate_badge.remove_all_products()
                self.candidate_badge.add_product(my_product)


# select min(i), decade from
# (select coalesce((post_counts->>'wikipedia')::int, 0) as i,
# ntile(10) over (order by coalesce((post_counts->>'wikipedia')::int, 0)) as decade
# from person
# where (campaign = 'impactstory_nos' or campaign = 'impactstory_subscribers')
# order by coalesce((post_counts->>'wikipedia')::int, 0) )
# s
# group by decade
# order by decade

# 0	1
# 0	2
# 0	3
# 0	4
# 0	5
# 1	6
# 1	7
# 3	8
# 4	9
# 9	10

# still trying
class wiki_hit(BadgeAssigner):
    display_name = "Wiki hit"
    is_for_products = False
    group = "channels"
    description = u"Your research is mentioned in {value} Wikipedia articles!"
    extra_description = "Wikipedia is referenced by <a href='http://www.theatlantic.com/health/archive/2014/03/doctors-1-source-for-healthcare-information-wikipedia/284206/'>half of doctors!</a>"
    importance = .9
    levels = [
        BadgeLevel(1, threshold=1),
        # BadgeLevel(2, threshold=2),
        # BadgeLevel(3, threshold=3),
        # BadgeLevel(4, threshold=5)
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        num_wikipedia_posts = person.post_counts_by_source("wikipedia")
        if num_wikipedia_posts > threshold:
            self.assigned = True
            self.candidate_badge.value = num_wikipedia_posts

            urls = person.wikipedia_urls
            self.candidate_badge.add_products([p for p in person.products if p.has_source("wikipedia")])
            self.candidate_badge.support = u"Wikipedia titles include: {}.".format(
                ", ".join(urls))
            # print self.candidate_badge.support

# 0	1
# 19134	2
# 35579	3
# 60996	4
# 91346	5
# 138575	6
# 186199	7
# 299688	8
# 495392	9
# 868292	10

# still working on this one

# inspired by https://github.com/ThinkUpLLC/ThinkUp/blob/db6fbdbcc133a4816da8e7cc622fd6f1ce534672/webapp/plugins/insightsgenerator/insights/followcountvisualizer.php
class impressions(BadgeAssigner):
    display_name = "You make an impression"
    is_for_products = False
    group = "reach"
    description = u"The number of twitter impressions your work would fill {value}!"
    importance = .91
    img_url = "https://en.wikipedia.org/wiki/File:Avery_fisher_hall.jpg"
    credit = "Photo: Mikhail Klassen"
    levels = [
        BadgeLevel(1, threshold=100),
        # BadgeLevel(1, threshold=2740),
        # BadgeLevel(2, threshold=10000),
        # BadgeLevel(3, threshold=50000),
        # BadgeLevel(4, threshold=250000),
        # BadgeLevel(5, threshold=500000)
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        if person.impressions > threshold:
            self.assigned = True
            self.candidate_badge.value = person.impressions


class babel(BadgeAssigner):
    display_name = "Babel"
    level = 1
    is_for_products = False
    group = "audience"
    description = u"Your impact is in {value} more languages than just English!"
    extra_description = "Due to issues with the Twitter API, we don't have language information for tweets yet."
    importance = .85
    levels = [
        BadgeLevel(1, threshold=1),
        # BadgeLevel(2, threshold=2),
        # BadgeLevel(3, threshold=3),
        # BadgeLevel(4, threshold=4),
        # BadgeLevel(5, threshold=5),
        # BadgeLevel(6, threshold=6),
        # BadgeLevel(7, threshold=7),
        # BadgeLevel(8, threshold=10),
        # BadgeLevel(9, threshold=12),
        # BadgeLevel(10, threshold=15)
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        languages_with_examples = {}

        for my_product in person.products:
            languages_with_examples.update(my_product.languages_with_examples)
            if len(set(my_product.languages_with_examples.keys()) - set(["en"])) > 0:
                self.candidate_badge.add_product(my_product)

        if len(languages_with_examples) >= threshold:
            self.assigned = True
            self.candidate_badge.value = len(languages_with_examples)
            language_url_list = [u"<a href='{}'>{}</a>".format(url, lang)
                 for (lang, url) in languages_with_examples.iteritems()]
            self.candidate_badge.support = u"Langauges: {}".format(u", ".join(language_url_list))
            # print self.candidate_badge.support


class global_reach(BadgeAssigner):
    display_name = "Global reach"
    level = 1
    is_for_products = False
    group = "geo"
    description = u"Your research has made an impact in more than {value} countries"
    importance = .85
    levels = [
        BadgeLevel(1, threshold=1),
        # BadgeLevel(2, threshold=2),
        # BadgeLevel(3, threshold=3),
        # BadgeLevel(4, threshold=5),
        # BadgeLevel(5, threshold=10),
        # BadgeLevel(6, threshold=15),
        # BadgeLevel(7, threshold=20),
        # BadgeLevel(8, threshold=25),
        # BadgeLevel(9, threshold=50),
        # BadgeLevel(10, threshold=75)
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        if len(person.countries) > threshold:
            self.assigned = True
            self.candidate_badge.value = len(person.countries)
            self.candidate_badge.support = u"Countries include: {}.".format(", ".join(person.countries))



class long_legs(BadgeAssigner):
    display_name = "Long Legs"
    level = 1
    is_for_products = True
    group = "timeline"
    description = u"Your research received news or blog mentions more than {value} months after it was published"
    importance = .5
    levels = [
        BadgeLevel(1, threshold=0.5),
        # BadgeLevel(2, threshold=1),
        # BadgeLevel(3, threshold=1.5),
        # BadgeLevel(4, threshold=2),
        # BadgeLevel(5, threshold=2.5),
        # BadgeLevel(6, threshold=3),
        # BadgeLevel(7, threshold=4)
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        self.candidate_badge.value = 0
        for my_product in person.products:
            for source, days_since_pub in my_product.event_days_since_publication.iteritems():
                if source in ["news", "blogs"]:
                    events_after_two_years = [e for e in days_since_pub if e > threshold*365]
                    if len(events_after_two_years) > self.candidate_badge.value:
                        self.assigned = True
                        self.candidate_badge.value = len(events_after_two_years)
                        self.candidate_badge.remove_all_products()
                        self.candidate_badge.add_product(my_product)



class megafan(BadgeAssigner):
    display_name = "Megafan"
    level = 1
    is_for_products = True
    group = "audience"
    description = u"Someone with more than {value} followers has tweeted your research."
    importance = .4
    levels = [
        BadgeLevel(1, threshold=100),
        # BadgeLevel(2, threshold=5000),
        # BadgeLevel(3, threshold=10000),
        # BadgeLevel(4, threshold=25000),
        # BadgeLevel(5, threshold=50000),
        # BadgeLevel(6, threshold=75000),
        # BadgeLevel(7, threshold=100000),
        # BadgeLevel(8, threshold=250000),
        # BadgeLevel(9, threshold=500000),
        # BadgeLevel(10, threshold=1000000),
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        fans = set()

        self.candidate_badge.value = 0
        for my_product in person.products:
            for fan_name, followers in my_product.twitter_posters_with_followers.iteritems():
                if followers >= self.candidate_badge.value:
                    self.assigned = True
                    self.candidate_badge.value = followers
                    self.candidate_badge.remove_all_products()  # clear them
                    self.candidate_badge.add_product(my_product)  # add the one for the new max
                    fans.add(fan_name)

        fan_urls = [u"<a href='http://twitter.com/{fan}'>@{fan}</a>".format(fan=fan) for fan in fans]
        self.candidate_badge.support = u"Megafans include: {}".format(u",".join(fan_urls))



class hot_streak(BadgeAssigner):
    display_name = "Hot streak"
    level = 1
    is_for_products = False
    group = "timeline"
    description = u"You made an impact in each of the last {value} months"
    importance = .7
    levels = [
        BadgeLevel(1, threshold=1),
        # BadgeLevel(1, threshold=3),
        # BadgeLevel(2, threshold=4),
        # BadgeLevel(3, threshold=5),
        # BadgeLevel(4, threshold=6),
        # BadgeLevel(5, threshold=9),
        # BadgeLevel(6, threshold=12),
        # BadgeLevel(7, threshold=18),
        # BadgeLevel(8, threshold=24),
        # BadgeLevel(9, threshold=36),
        # BadgeLevel(10, threshold=48),
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        streak = True
        for month in range(0, threshold):
            matching_days_count = 0
            for source, days_ago in person.all_event_days_ago.iteritems():
                relevant_days = [month*30 + day for day in range(0, 30)]
                matching_days_count += len([d for d in days_ago if d in relevant_days])

            if matching_days_count <= 0:
                # print "broke the streak on month", month
                streak = False
        if streak:
            self.assigned = True
            self.candidate_badge.value = month



class deep_interest(BadgeAssigner):
    display_name = "Deep interest"
    level = 1
    is_for_products = True
    group = "channels"
    description = u"People are deeply interested in your research.  Your ratio of (news + blogs) / (twitter + facebook) is {value}"
    extra_description = "Based on papers published since 2012 that have more than 10 relevant posts."
    importance = .4
    levels = [
        BadgeLevel(1, threshold=.001),
        # BadgeLevel(2, threshold=.1),
        # BadgeLevel(3, threshold=.15),
        # BadgeLevel(4, threshold=.2),
        # BadgeLevel(5, threshold=.25),
        # BadgeLevel(6, threshold=.3),
        # BadgeLevel(7, threshold=.4),
        # BadgeLevel(8, threshold=.5),
        # BadgeLevel(9, threshold=.6),
        # BadgeLevel(10, threshold=.7),
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        self.candidate_badge.value = 0
        for my_product in person.products:
            longform_posts = 0.0
            shortform_posts = 0.0

            if my_product.year_int > 2011:
                longform_posts += my_product.post_counts_by_source("news")
                longform_posts += my_product.post_counts_by_source("blogs")
                shortform_posts += my_product.post_counts_by_source("twitter")
                shortform_posts += my_product.post_counts_by_source("facebook")

            if (shortform_posts > 0) and (longform_posts+shortform_posts > 10):
                ratio = longform_posts / shortform_posts
                # print u"deep-interest ratio: ", ratio
                if ratio >= self.candidate_badge.value:
                    self.assigned = True
                    self.candidate_badge.value = ratio
                    self.candidate_badge.remove_all_products()
                    self.candidate_badge.add_product(my_product)


class clean_sweep(BadgeAssigner):
    display_name = "Clean sweep"
    level = 1
    is_for_products = False
    group = "timeline"
    description = "All of your publications since 2012 have made impact, with at least {value} altmetric score."
    importance = .2
    levels = [
        BadgeLevel(1, threshold=1),
        # BadgeLevel(2, threshold=2),
        # BadgeLevel(3, threshold=3),
        # BadgeLevel(4, threshold=4),
        # BadgeLevel(5, threshold=5),
        # BadgeLevel(6, threshold=6),
        # BadgeLevel(7, threshold=7),
        # BadgeLevel(8, threshold=8),
        # BadgeLevel(9, threshold=9),
        # BadgeLevel(10, threshold=10),
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        num_with_posts = 0
        num_applicable = 0
        for my_product in person.products:
            if my_product.year > 2011:
                num_applicable += 1
                if my_product.altmetric_score >= threshold:
                    num_with_posts += 1
                    self.candidate_badge.add_product(my_product)

        if num_with_posts >= num_applicable:
            self.assigned = True
            self.candidate_badge.value = num_with_posts



class global_south(BadgeAssigner):
    display_name = "Global South"
    level = 1
    is_for_products = True
    group = "geo"
    description = u"More than {value}% of your impact is from the Global South."
    importance = .5
    levels = [
        BadgeLevel(1, threshold=.001),
        # BadgeLevel(2, threshold=.1),
        # BadgeLevel(3, threshold=.15),
        # BadgeLevel(4, threshold=.2),
        # BadgeLevel(5, threshold=.25),
        # BadgeLevel(6, threshold=.3),
        # BadgeLevel(7, threshold=.4),
        # BadgeLevel(8, threshold=.5),
        # BadgeLevel(9, threshold=.6),
        # BadgeLevel(10, threshold=.7),
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        countries = []

        total_geo_located_posts = 0.0
        total_global_south_posts = 0.0

        for my_product in person.products:
            for country_iso, count in my_product.post_counts_by_country.iteritems():
                total_geo_located_posts += count
                country_name = get_name_from_iso(country_iso)
                if country_name:
                    try:
                        if country_info[country_name]["is_global_south"]:
                            total_global_south_posts += count
                            self.candidate_badge.add_product(my_product)
                            countries.append(country_name)
                    except KeyError:
                        print u"ERROR: Nothing in dict for country name {}".format(country_name)
                        raise # don't keep going

        if total_geo_located_posts > 0:
            # print u"PERCENT GLOBAL SOUTH {} / {} = {}".format(
            #     total_global_south_posts,
            #     total_geo_located_posts,
            #     (total_global_south_posts / total_geo_located_posts)
            # )
            # print u"global south countries: {}".format(countries)

            ratio = (total_global_south_posts / total_geo_located_posts)
            if ratio > threshold:
                self.assigned = True
                self.candidate_badge.value = 100.0 * ratio
                self.candidate_badge.support = "Impact from these Global South countries: {}.".format(
                    ", ".join(countries))




#############
# SINGLES
#############



class pacific_rim(BadgeAssigner):
    display_name = "Pacific rim"
    level = 1
    is_for_products = True
    group = "geo"
    description = u"You have impact from at least three eastern Pacific Rim and three western Pacific Rim countries."
    importance = .2

    def decide_if_assigned(self, person):
        countries = []

        num_pacific_rim_west = 0
        for country in pacific_rim_west:
            matching_products = [p for p in person.products if p.has_country(country)]
            if matching_products:
                num_pacific_rim_west += 1
                self.candidate_badge.add_products(matching_products)
                countries.append(country)

        num_pacific_rim_east = 0
        for country in pacific_rim_east:
            matching_products = [p for p in person.products if p.has_country(country)]
            if matching_products:
                num_pacific_rim_east += 1
                self.candidate_badge.add_products(matching_products)
                countries.append(country)

        if num_pacific_rim_west >= 3 and num_pacific_rim_east >= 3:
            self.assigned = True
            self.candidate_badge.value = 1
            self.candidate_badge.support = "Impact from these Pacific Rim countries: {}.".format(
                ", ".join(countries))
            # print u"badge support: {}".format(self.candidate_badge.support)



class ivory_tower(BadgeAssigner):
    display_name = "Ivory Tower"
    level = 1
    is_for_products = False
    group = "audience"
    description = u"More than {value} of your impact is from other researchers."
    importance = .1

    def decide_if_assigned(self, person):
        proportion = proportion_poster_counts_by_type(person, "Scientists")
        if proportion > 0.01:
            self.assigned = True
            self.candidate_badge.value = proportion * 100


class practical_magic(BadgeAssigner):
    display_name = "Practical Magic"
    level = 1
    is_for_products = False
    group = "audience"
    description = u"More than {value} of your impact is from practitioners."
    importance = .6

    def decide_if_assigned(self, person):
        proportion = proportion_poster_counts_by_type(person, "Practitioners (doctors, other healthcare professionals)")
        if proportion > 0.01:
            self.assigned = True
            self.candidate_badge.value = proportion * 100

class press_pass(BadgeAssigner):
    display_name = "Press pass"
    level = 1
    is_for_products = False
    group = "audience"
    description = u"More than {value} of your impact is from science communicators."
    importance = .25

    def decide_if_assigned(self, person):
        proportion = proportion_poster_counts_by_type(person, "Science communicators (journalists, bloggers, editors)")
        if proportion > 0.01:
            self.assigned = True
            self.candidate_badge.value = proportion * 100


class special_interests(BadgeAssigner):
    display_name = "Special interests"
    level = 1
    is_for_products = False
    group = "audience"
    description = u"{value}% of your audience is a special demo"
    importance = .25

    def decide_if_assigned(self, person):
        proportion = proportion_poster_counts_by_type(person, "Science communicators (journalists, bloggers, editors)") + \
            proportion_poster_counts_by_type(person, "Scientists") + \
            proportion_poster_counts_by_type(person, "Practitioners (doctors, other healthcare professionals)")

        if proportion > 0.01:
            self.assigned = True
            self.candidate_badge.value = proportion * 100


class sleeping_beauty(BadgeAssigner):
    display_name = "Sleeping beauty"
    level = 1
    is_for_products = True
    group = "timeline"
    description = u"Your research picked up in activity after its first six months, with a ratio of {value}"
    importance = .6

    def decide_if_assigned(self, person):
        for my_product in person.products:
            events_with_dates = 0.0
            events_in_first_six_months = 0.0

            for source, days_since_pub in my_product.event_days_since_publication.iteritems():
                events_with_dates += len(days_since_pub)
                events_in_first_six_months += len([e for e in days_since_pub if e <= 180])

            if events_with_dates > 0:
                ratio = events_in_first_six_months / events_with_dates
                if ratio <= 0.5:
                    self.assigned = True
                    self.candidate_badge.value = ratio
                    self.candidate_badge.add_product(my_product)


def proportion_poster_counts_by_type(person, poster_type):
    total_posters_with_type = 0.0
    my_type = 0.0
    for my_product in person.products:
        total_posters_with_type += sum(my_product.poster_counts_by_type.values())
        if poster_type in my_product.poster_counts_by_type:
            my_type += my_product.poster_counts_by_type[poster_type]

    if total_posters_with_type:
        return (my_type / total_posters_with_type)
    else:
        return 0


class good_for_teaching(BadgeAssigner):
    display_name = "Good for teaching"
    level = 1
    is_for_products = True
    group = "merit"
    description = u"Cool! An F1000 reviewer called your research good for teaching"
    importance = .4

    def decide_if_assigned(self, person):
        urls = []
        for my_product in person.products:
            f1000_urls = my_product.f1000_urls_for_class("good_for_teaching")
            if f1000_urls:
                self.assigned = True
                self.candidate_badge.add_product(my_product)
                urls += f1000_urls

        if self.assigned:
            self.candidate_badge.value = 1
            self.candidate_badge.support = u"The F1000 reviews include: {}.".format(
                ", ".join(urls))
            # print self.candidate_badge.support




class publons(BadgeAssigner):
    display_name = "Publons star"
    level = 1
    is_for_products = True
    group = "merit"
    description = u"Your research has a great score on Publons!"
    importance = .7

    def decide_if_assigned(self, person):
        reviews = []

        if person.post_counts_by_source("peer_reviews") >= 1:
            for my_product in person.products:
                for review in my_product.publons_reviews:
                    if review["publons_weighted_average"] > 5:
                        self.assigned = True
                        self.candidate_badge.add_product(my_product)
                        reviews.append(review)
        if self.assigned:
            self.candidate_badge.value = 1
            review_urls = [u"<a href='{}'>Review</a>".format(review["url"]) for review in reviews]
            self.candidate_badge.support = u"Publons reviews: {}".format(", ".join(review_urls))


class first_steps(BadgeAssigner):
    display_name = "First steps"
    level = 1
    is_for_products = False
    group = "reach"
    description = u"You have made online impact!  Congrats!"
    importance = .01

    def decide_if_assigned(self, person):
        for my_product in person.products:
            if my_product.altmetric_score > 0:
                self.assigned = True
                self.candidate_badge.value = 1


#############
# FUN
#############

class url_soup(BadgeAssigner):
    display_name = "URL soup"
    level = 1
    is_for_products = True
    group = "fun"
    description = u"You have a research product that has made impact under more than 20 urls"

    def decide_if_assigned(self, person):
        self.candidate_badge.value = 0
        for my_product in person.products:
            if len(my_product.impact_urls) > 20 and len(my_product.impact_urls) > self.candidate_badge.value:
                self.assigned = True
                self.candidate_badge.value = len(my_product.impact_urls)

                self.candidate_badge.add_product(my_product)
                self.candidate_badge.support = u"URLs for one of the products include: {}".format(
                    ", ".join(sorted(my_product.impact_urls))
                )


class bff(BadgeAssigner):
    display_name = "bff"
    level = 1
    is_for_products = False
    group = "fun"
    description = u"You have a BFF! Someone has tweeted three or more of your papers."

    def decide_if_assigned(self, person):
        fan_counts = defaultdict(int)
        fans = set()

        for my_product in person.products:
            for fan_name in my_product.twitter_posters_with_followers:
                fan_counts[fan_name] += 1

        for fan_name, tweeted_papers_count in fan_counts.iteritems():
            if tweeted_papers_count >= 3:
                self.assigned = True
                fans.add(fan_name)

        if self.assigned:
            self.candidate_badge.value = len(fans)
            fan_urls = [u"<a href='http://twitter.com/{fan}'>@{fan}</a>".format(fan=fan) for fan in fans]
            self.candidate_badge.support = u"BFFs include: {}".format(u",".join(fan_urls))

class rick_roll(BadgeAssigner):
    display_name = "Rickroll"
    level = 1
    is_for_products = True
    group = "fun"
    description = u"You have been tweeted by a person named Richard!"
    video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def decide_if_assigned(self, person):
        for my_product in person.products:
            for name in my_product.tweeter_posters_full_names:
                match = False
                if name.lower().endswith("richard"):
                    match = True
                else:
                    for name_part in name.lower().split(" ")[:-1]:  # don't include last name
                        if name_part in ["rick", "rich", "ricky", "dick", "richard"]:
                            match = True
                if match:
                    self.assigned = True
                    self.candidate_badge.value = 1
                    self.candidate_badge.add_product(my_product)
                    self.candidate_badge.support = u"Thanks, {}".format(name)

        # if self.assigned:
        #     print "RICK!!!!", self.candidate_badge.support


class big_in_japan(BadgeAssigner):
    display_name = "Big in Japan"
    level = 1
    is_for_products = True
    group = "fun"
    description = u"You made impact in Japan!"
    video_url = "https://www.youtube.com/watch?v=tl6u2NASUzU"
    credit = 'Alphaville - "Big In Japan"'

    def decide_if_assigned(self, person):
        for my_product in person.products:
            if my_product.has_country("Japan"):
                self.candidate_badge.add_product(my_product)
                self.assigned = True
                self.candidate_badge.value = 1


class controversial(BadgeAssigner):
    display_name = "Controversial"
    level = 1
    is_for_products = True
    group = "fun"
    description = u"Cool! An F1000 reviewer called your research Controversial!"

    def decide_if_assigned(self, person):
        urls = []
        for my_product in person.products:
            f1000_urls = my_product.f1000_urls_for_class("controversial")
            if f1000_urls:
                self.assigned = True
                self.candidate_badge.add_product(my_product)
                urls += f1000_urls

        if self.assigned:
            self.candidate_badge.value = 1
            self.candidate_badge.support = u"The F1000 reviews include: {}.".format(
                ", ".join(urls))
            # print self.candidate_badge.support


class famous_follower(BadgeAssigner):
    display_name = "Famous follower"
    level = 1
    is_for_products = True
    group = "fun"
    description = u"You have been tweeted by {value} well-known scientists"
    levels = [
        BadgeLevel(1, threshold=1),
        # BadgeLevel(2, threshold=2),
        # BadgeLevel(3, threshold=3),
        # BadgeLevel(4, threshold=4),
        # BadgeLevel(5, threshold=5),
        # BadgeLevel(6, threshold=6),
        # BadgeLevel(7, threshold=7),
        # BadgeLevel(8, threshold=8),
        # BadgeLevel(9, threshold=9),
        # BadgeLevel(10, threshold=10)
    ]

    def decide_if_assigned_threshold(self, person, threshold):
        fans = set()
        for my_product in person.products:
            for twitter_handle in my_product.twitter_posters_with_followers:
                try:
                    if twitter_handle.lower() in scientists_twitter:
                        fans.add(twitter_handle)
                        self.candidate_badge.add_product(my_product)
                except AttributeError:
                    pass

        if len(fans) > threshold:
            self.assigned = True
            self.candidate_badge.value = len(fans)
            fan_urls = [u"<a href='http://twitter.com/{fan}'>@{fan}</a>".format(fan=fan) for fan in fans]
            self.candidate_badge.support = u"Famous fans include: {}".format(u",".join(fan_urls))


