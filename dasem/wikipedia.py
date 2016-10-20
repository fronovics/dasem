#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Wikipedia interface.

Usage:
  dasem.wikipedia category-graph | count-category-pages
  dasem.wikipedia count-pages | count-pages-per-user
  dasem.wikipedia article-link-graph [options]
  dasem.wikipedia iter-pages | iter-article-words [options]
  dasem.wikipedia doc-term-matrix [options]
  dasem.wikipedia save-tfidf-vectorizer [options]

Options:
  -h --help            Help
  -v --verbose         Verbose messages
  --display
  --max-n-pages=<int>  Maximum number of pages to iterate over
  --filename=<str>     Filename

"""

from __future__ import division, print_function

from bz2 import BZ2File

import codecs

from collections import Counter

import os.path

import re

import json

import gensim

import gzip

try:
    import cPickle as pickle
except ImportError:
    import pickle

import jsonpickle
import jsonpickle.ext.numpy as jsonpickle_numpy

from lxml import etree

import mwparserfromhell

import numpy as np

from scipy.sparse import lil_matrix

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer

from tqdm import tqdm

from .config import data_directory
from .text import sentence_tokenize, word_tokenize


jsonpickle_numpy.register_handlers()


BZ2_XML_DUMP_FILENAME = 'dawiki-20160901-pages-articles.xml.bz2'

TFIDF_VECTORIZER_FILENAME = 'wikipedia-tfidfvectorizer.json'

WORD2VEC_FILENAME = 'wikipedia-word2vec.pkl.gz'

ESA_PKL_FILENAME = 'wikipedia-esa.pkl.gz'

ESA_JSON_FILENAME = 'wikipedia-esa.json.gz'


def is_article_link(wikilink):
    """Return True is wikilink is an article link.

    Parameters
    ----------
    wikilink : str
        Wikilink to be tested

    Returns
    -------
    result : bool
        True is wikilink is an article link

    Examples
    --------
    >>> is_article_link('[[Danmark]]')
    True
    >>> is_article_link('[[Kategori:Danmark]]')
    False

    """
    if wikilink.startswith('[[') and len(wikilink) > 4:
        wikilink = wikilink[2:]
    if not (wikilink.startswith('Diskussion:')
            or wikilink.startswith('Fil:')
            or wikilink.startswith('File:')
            or wikilink.startswith('Kategori:')
            or wikilink.startswith('Kategoridiskussion:')
            or wikilink.startswith('Wikipedia:')
            or wikilink.startswith('Wikipedia-diskussion:')
            or wikilink.startswith(u'Hjælp:')
            or wikilink.startswith(u'Hjælp-diskussion')
            or wikilink.startswith('Bruger:')
            or wikilink.startswith('Brugerdiskussion:')):
        return True
    return False


def strip_wikilink_to_article(wikilink):
    """Strip wikilink to article.

    Parameters
    ----------
    wikilink : str
        Wikilink

    Returns
    -------
    stripped_wikilink : str
        String with stripped wikilink.

    Examples
    --------
    >>> strip_wikilink_to_article('[[dansk (sprog)|dansk]]')
    'dansk (sprog)'

    >>> strip_wikilink_to_article('Danmark')
    'Danmark'

    """
    if wikilink.startswith('[['):
        wikilink = wikilink[2:-2]
    return wikilink.split('|')[0]


def strip_to_category(category):
    """Strip prefix and postfix from category link.

    Parameters
    ----------
    category : str

    Returns
    -------
    stripped_category : str
        String with stripped category

    """
    if category.startswith('[[Kategori:'):
        category = category[11:-2]
    elif category.startswith('Kategori:'):
        category = category[9:]
    return category.split('|')[0]


class XmlDumpFile(object):
    """XML Dump file.

    For instance, dawiki-20160901-pages-articles.xml.bz2.

    Attributes
    ----------
    file : file
        File object to read from.
    filename : str
        Filename of dump file.
    word_pattern : _sre.SRE_Pattern
        Compile regular expressions for finding words.

    """

    def __init__(self, filename=BZ2_XML_DUMP_FILENAME):
        """Prepare dump file for reading.

        Parameters
        ----------
        filename : str
            Filename or the XML dump file.

        """
        full_filename = self.full_filename(filename)
        self.filename = full_filename

        self.word_pattern = re.compile(
            r"""{{.+?}}|
            <!--.+?-->|
            \[\[Fil.+?\]\]|
            \[\[Kategori:.+?\]\]|
            \[http.+?\]|(\w+(?:-\w+)*)""",
            flags=re.UNICODE | re.VERBOSE | re.DOTALL)

    def full_filename(self, filename):
        """Return filename with full filename path."""
        if os.path.sep in filename:
            return filename
        else:
            return os.path.join(data_directory(), 'wikipedia', filename)

    def clean_tag(self, tag):
        """Remove namespace from tag.

        Parameters
        ----------
        tag : str
            Tag with namespace prefix.

        Returns
        -------
        cleaned_tag : str
            Tag with namespace part removed.

        """
        cleaned_tag = tag.split('}')[-1]
        return cleaned_tag

    def iter_elements(self, events=('end',)):
        """Iterate over elements in XML file.

        Yields
        ------
        event : str
            'start' or 'end'
        element : Element
            XML element

        """
        if self.filename.endswith('.bz2'):
            self.file = BZ2File(self.filename)
        else:
            self.file = file(self.filename)

        with self.file as f:
            for event, element in etree.iterparse(f, events=events):
                yield event, element

    def iter_page_elements(self, events=('end',)):
        """Iterator for page XML elements."""
        for event, element in self.iter_elements(events=events):
            tag = self.clean_tag(element.tag)
            if tag == 'page':
                yield event, element

    def iter_pages(self):
        """Iterator for page yielding a dictionary.

        Yields
        ------
        page : dict

        """
        for event, element in self.iter_page_elements(events=('end',)):
            page = {}
            for descendant in element.iterdescendants():
                tag = self.clean_tag(descendant.tag)
                if tag not in ['contributor', 'revision']:
                    page[tag] = descendant.text
            yield page

    def count_pages(self):
        """Return number of pages.

        Returns
        -------
        count : int
            Number of pages

        """
        count = 0
        for event, element in self.iter_page_elements():
            count += 1
        return count

    def count_pages_per_user(self):
        """Count the number of pages per user.

        Counts for both 'username' and 'ip' are recorded.

        Returns
        -------
        counts : collections.Counter
            Counter object containing counts as values.

        """
        counts = Counter()
        for page in self.iter_pages():
            if 'username' in page:
                counts[page['username']] += 1
            elif 'ip' in page:
                counts[page['ip']] += 1
        return counts

    def iter_article_pages(self, max_n_pages=None, display=False):
        """Iterate over article pages.

        Parameters
        ----------
        max_n_pages : int or None
            Maximum number of pages to return.

        Yields
        ------
        page : dict

        """
        n = 0
        for page in tqdm(self.iter_pages(), disable=not display):
            if page['ns'] == '0':
                n += 1
                yield page
                if max_n_pages is not None and n >= max_n_pages:
                    break

    def iter_article_sentences(self, max_n_pages=None, display=False):
        """Iterate over article sentences.

        Parameters
        ----------
        max_n_pages : int or None, optional
            Maximum number of pages to return.
        display : bool, optional
            Print iteration feedback information

        Yields
        ------
        sentences : str
            Sentences as strings.

        """
        n = 0
        for article in tqdm(self.iter_article_pages(
                max_n_pages=max_n_pages, display=display),
                         disable=not display):
            n += 1
            text = mwparserfromhell.parse(article['text'])
            sentences = sentence_tokenize(text.strip_code())
            for sentence in sentences:
                yield sentence
            if max_n_pages is not None and n >= max_n_pages:
                break

    def iter_article_sentence_words(
            self, lower=True, max_n_pages=None, display=False):
        """Iterate over article sentences.

        Parameters
        ----------
        lower : bool, optional
            Lower case words
        max_n_pages : int or None, optional
            Maximum number of pages to return.
        display : bool, optional
            Print iteration feedback information

        Yields
        ------
        sentences : list of str
            Sentences as list of words represented as strings.

        """
        n = 0
        for article in tqdm(self.iter_article_pages(
                max_n_pages=max_n_pages, display=display),
                         disable=not display):
            n += 1
            text = mwparserfromhell.parse(article['text'])
            sentences = sentence_tokenize(text.strip_code())
            for sentence in sentences:
                tokens = word_tokenize(sentence)
                if lower:
                    yield [token.lower() for token in tokens]
                else:
                    yield tokens

            if max_n_pages is not None and n >= max_n_pages:
                break

    def iter_article_words(self, max_n_pages=None):
        """Iterate over articles returning word list.

        Parameters
        ----------
        max_n_pages : int or None
            Maximum number of pages to iterate over.

        Yields
        ------
        title : str
            Title of article
        words : list of str
            List of words

        """
        for page in self.iter_article_pages(max_n_pages=max_n_pages):
            words = self.word_pattern.findall(page['text'])
            words = [word.lower() for word in words if word]
            yield page['title'], words

    def article_link_graph(self, verbose=False):
        """Return article link graph.

        Returns
        -------
        graph : dict
            Dictionary with values as a list where elements indicate
            article linked to.

        """
        graph = {}
        for n, page in enumerate(self.iter_article_pages()):
            wikicode = mwparserfromhell.parse(page['text'])
            wikilinks = wikicode.filter_wikilinks()
            article_links = []
            for wikilink in wikilinks:
                if is_article_link(wikilink):
                    article_link = strip_wikilink_to_article(wikilink)
                    article_links.append(article_link.title())
            graph[page['title']] = article_links
            if verbose and not n % 100:
                print(n)
        return graph

    def iter_category_pages(self):
        """Iterate over category pages.

        For dawiki-20160901-pages-articles.xml.bz2 this method
        returns 51548

        Yields
        ------
        page : dict

        """
        for page in self.iter_pages():
            if page['ns'] == '14':
                yield page

    def count_category_pages(self):
        """Count category pages.

        Returns
        -------
        count : int
            Number of category pages.

        """
        n = 0
        for page in self.iter_category_pages():
            n += 1
        return n

    def category_graph(self):
        """Return category graph.

        Returns
        -------
        graph : dict
            Dictionary with values indicating supercategories.

        """
        graph = {}
        for page in self.iter_category_pages():
            wikicode = mwparserfromhell.parse(page['text'])
            wikilinks = wikicode.filter_wikilinks()
            categories = []
            for wikilink in wikilinks:
                if wikilink.startswith('[[Kategori:'):
                    categories.append(strip_to_category(wikilink))
            category = strip_to_category(page['title'])
            graph[category] = categories
        return graph

    def doc_term_matrix(self, max_n_pages=None, verbose=False):
        """Return doc-term matrix.

        Parameters
        ----------
        max_n_pages : int or None
            Maximum number of Wikipedia articles to iterate over.
        verbose : bool
            Display message during processing.

        """
        # Identify terms
        n_pages = 0
        all_terms = []
        for title, words in self.iter_article_words(max_n_pages=max_n_pages):
            n_pages += 1
            all_terms.extend(words)
            if verbose and not n_pages % 100:
                print(u"Identified terms from article {}".format(n_pages))
        terms = list(set(all_terms))
        n_terms = len(terms)

        if verbose:
            print("Constructing sparse matrix of size {}x{}".format(
                n_pages, n_terms))
        matrix = lil_matrix((n_pages, n_terms))

        # Count terms wrt. articles
        rows = []
        columns = dict(zip(terms, range(len(terms))))
        for n, (title, words) in enumerate(self.iter_article_words(
                max_n_pages=max_n_pages)):
            rows.append(title)
            for word in words:
                matrix[n, columns[word]] += 1
            if verbose and not n % 100:
                print(u"Sat counts in matrix from article {}".format(n))

        return matrix, rows, terms


class ExplicitSemanticAnalysis(object):
    """Explicit semantic analysis.

    References
    ----------
    Evgeniy Gabrilovich, Shaul Markovitch, Computing semantic relatedness
    using Wikipedia-based explicit semantic analysis, 2007.

    """
    
    def __init__(
            self, autosetup=True, stop_words=None, norm='l2', use_idf=True,
            sublinear_tf=False, max_n_pages=None, display=False):
        """Setup model.

        Several of the parameters are piped further on to sklearns
        TfidfVectorizer.

        Parameters
        ----------
        stop_words : list of str or None, optional
            List of stop words.
        norm : 'l1', 'l2' or None, optional
            Norm use to normalize term vectors of tfidf vectorizer.
        use_idf : bool, optional
            Enable inverse-document-frequency reweighting.

        """
        if autosetup:
            try:
                self.load_pkl(display=display)
            except:
                self.setup(
                    stop_words=stop_words, norm=norm, use_idf=use_idf,
                    sublinear_tf=sublinear_tf, max_n_pages=max_n_pages,
                    display=display)
                self.save_pkl(display=display)

    def full_filename(self, filename):
        """Return filename with full filename path."""
        if os.path.sep in filename:
            return filename
        else:
            return os.path.join(data_directory(), 'models', filename)

    def save_json(self, filename=ESA_JSON_FILENAME, display=False):
        full_filename = self.full_filename(filename)
        if display:
            tqdm.write('Writing parameters to JSON file {}'.format(
                full_filename))
        with gzip.open(full_filename, 'w') as f:
            f.write(jsonpickle.encode(
                {'Y': self._Y,
                 'transformer' :self._transformer,
                 'titles': self._titles}))

    def load_json(self, filename=ESA_JSON_FILENAME, display=False):
        """Load model parameters from JSON pickle file.

        Parameters
        ----------
        filename : str
            Filename for gzipped JSON pickled file.

        """
        full_filename = self.full_filename(filename)
        if display:
            tqdm.write('Reading parameters from JSON file {}'.format(
                full_filename))
        with gzip.open(full_filename) as f:
            data = jsonpickle.decode(f.read())

        self._Y = data['Y']
        self._transformer = data['transformer']
        self._titles = data['titles']
            
    def save_pkl(self, display=False):
        """Save parameters to pickle files."""
        items = [
            ('_titles', 'wikipedia-esa-titles.pkl.gz'),
            ('_Y', 'wikipedia-esa-y.pkl.gz'),
            ('_transformer', 'wikipedia-esa-transformer.pkl.gz')
        ]
        for attr, filename in items:
            full_filename = self.full_filename(filename) 
            if display:
                tqdm.write('Writing parameters to pickle file {}'.format(
                    full_filename))
            with gzip.open(full_filename, 'w') as f:
                pickle.dump(getattr(self, attr), f, -1)

    def load_pkl(self, display=False):
        """Load parameters from pickle files."""
        items = [
            ('_titles', 'wikipedia-esa-titles.pkl.gz'),
            ('_Y', 'wikipedia-esa-y.pkl.gz'),
            ('_transformer', 'wikipedia-esa-transformer.pkl.gz')
        ]
        for attr, filename in items:
            full_filename = self.full_filename(filename) 
            if display:
                tqdm.write('Reading parameters from pickle file {}'.format(
                    full_filename))
            with gzip.open(full_filename) as f:
                setattr(self, attr, pickle.load(f))
                
    def setup(
            self, stop_words=None, norm='l2', use_idf=True, sublinear_tf=False,
            max_n_pages=None, display=False):
        """Setup wikipedia semantic model.

        Returns
        -------
        self : ExplicitSemanticAnalysis
            Self object.
        
        """
        self._dump_file = XmlDumpFile()

        self._titles = [
            page['title'] for page in self._dump_file.iter_article_pages(
                max_n_pages=max_n_pages,
                display=display)]

        texts = (page['text']
                 for page in self._dump_file.iter_article_pages(
                         max_n_pages=max_n_pages,
                         display=display))

        if display:
            tqdm.write('TFIDF vectorizing')
        self._transformer = TfidfVectorizer(
            stop_words=stop_words, norm=norm, use_idf=use_idf,
            sublinear_tf=sublinear_tf)
        self._Y = self._transformer.fit_transform(texts)

        return self
        
    def relatedness(self, phrases):
        """Return semantic relatedness between two phrases.

        Parameters
        ----------
        phrases : list of str
            List of phrases as strings.

        Returns
        -------
        relatedness : np.array
            Array with value between 0 and 1 for semantic relatedness.

        """
        Y = self._transformer.transform(phrases)
        D = np.asarray((self._Y * Y.T).todense())
        D = np.einsum('ij,j->ij', D,
                      1 / np.sqrt(np.multiply(D, D).sum(axis=0)))
        return D.T.dot(D)

    def related(self, phrase, n=10):
        """Return related articles.

        Parameters
        ----------
        phrase : str
            Phrase
        n : int
            Number of articles to return.

        Returns
        -------
        titles : list of str
            List of articles as strings.

        """
        if n is None:
            n = 10
        y = self._transformer.transform([phrase])
        D = np.array((self._Y * y.T).todense())
        indices = np.argsort(-D, axis=0)
        titles = [self._titles[index] for index in indices[:n, 0]]
        return titles

    def sort_by_outlierness(self, phrases):
        """Return phrases based on outlierness.

        Parameters
        ----------
        phrases : list of str
            List of phrases.

        Returns
        -------
        sorted_phrases : list of str
            List of sorted phrases.

        Examples
        --------
        >>> esa = ExplicitSemanticAnalysis()
        >>> esa.sort_by_outlierness(['hund', 'fogh', 'nyrup', 'helle'])
        ['hund', 'nyrup', 'fogh', 'rasmussen']

        """
        R = self.relatedness(phrases)
        indices = np.argsort(R.sum(axis=0) - 1)
        return [phrases[idx] for idx in indices]
    

class Word2Vec(object):
    """Gensim Word2vec for Danish Wikipedia corpus."""

    class Sentences():
        """Sentence iterable.

        References
        ----------
        https://stackoverflow.com/questions/34166369

        """

        def __init__(self, lower=True, max_n_pages=None, display=False):
            """Setup parameters."""
            self.lower = lower
            self.max_n_pages = max_n_pages
            self.display = display

        def __iter__(self):
            """Restart and return iterable."""
            dump_file = XmlDumpFile()
            sentences = dump_file.iter_article_sentence_words(
                lower=self.lower,
                max_n_pages=self.max_n_pages,
                display=self.display)
            return sentences

    def __init__(self, autosetup=True):
        """Setup model.

        Parameters
        ----------
        autosetup : bool, optional
            Determines whether the Word2Vec model should be autoloaded.

        """
        self.model = None
        if autosetup:
            try:
                self.load()
            except:
                self.train()
                self.save()

    def full_filename(self, filename):
        """Return filename with full filename path."""
        if os.path.sep in filename:
            return filename
        else:
            return os.path.join(data_directory(), 'models', filename)

    def load(self, filename=WORD2VEC_FILENAME):
        """Load model from pickle file.

        This function is unsafe. Do not load unsafe files.

        Parameters
        ----------
        filename : str
            Filename of pickle file.

        """
        full_filename = self.full_filename(filename)
        self.model = gensim.models.Word2Vec.load(full_filename)

    def save(self, filename=WORD2VEC_FILENAME):
        """Save model to pickle file.

        The Gensim load file is used which can also compress the file.

        Parameters
        ----------
        filename : str, optional
            Filename.

        """
        full_filename = self.full_filename(filename)
        self.model.save(full_filename)

    def train(self, size=100, window=5, min_count=5, workers=4,
              max_n_pages=None, display=False):
        """Train Gensim Word2Vec model.

        Parameters
        ----------
        size : int, optional
            Dimension of the word2vec space.

        """
        sentences = Word2Vec.Sentences(
            max_n_pages=max_n_pages, display=display)
        self.model = gensim.models.Word2Vec(
            sentences, size=size, window=window, min_count=min_count,
            workers=workers)

    def doesnt_match(self, words):
        """Return odd word of list.

        Parameters
        ----------
        words : list of str
            List of words represented as strings.

        Returns
        -------
        word : str
            Outlier word.

        Examples
        --------
        >>> w2v = Word2Vec()
        >>> w2v.doesnt_match(['svend', 'stol', 'ole', 'anders'])
        'stol'

        """
        return self.model.doesnt_match(words)

    def most_similar(self, positive=[], negative=[], topn=10,
                     restrict_vocab=None, indexer=None):
        """Return most similar words."""
        return self.model.most_similar(
            positive, negative, topn, restrict_vocab, indexer)

    def similarity(self, word1, word2):
        """Return value for similarity between two words.

        Parameters
        ----------
        word1 : str
            First word to be compared
        word2 : str
            Second word.

        Returns
        -------
        value : float
            Similarity as a float value between 0 and 1.

        """
        return self.model.similarity(word1, word2)


def main():
    """Handle command-line interface."""
    from docopt import docopt

    arguments = docopt(__doc__)
    display = arguments['--display']
    if arguments['--max-n-pages'] is None:
        max_n_pages = None
    else:
        max_n_pages = int(arguments['--max-n-pages'])
    verbose = arguments['--verbose']

    dump_file = XmlDumpFile()

    if arguments['iter-pages']:
        for page in dump_file.iter_pages():
            print(json.dumps(page))

    elif arguments['count-pages']:
        count = dump_file.count_pages()
        print(count)

    elif arguments['count-pages-per-user']:
        counts = dump_file.count_pages_per_user().most_common(100)
        for n, (user, count) in enumerate(counts, 1):
            print(u"{:4} {:6} {}".format(n, count, user))

    elif arguments['article-link-graph']:
        graph = dump_file.article_link_graph(
            verbose=verbose)
        print(graph)

    elif arguments['category-graph']:
        graph = dump_file.category_graph()
        print(graph)

    elif arguments['count-category-pages']:
        count = dump_file.count_category_pages()
        print(count)

    elif arguments['iter-article-words']:
        for title, words in dump_file.iter_article_words(
                max_n_pages=int(arguments['--max-n-pages'])):
            print(json.dumps([title, words]))

    elif arguments['doc-term-matrix']:
        matrix, rows, columns = dump_file.doc_term_matrix(
            max_n_pages=int(arguments['--max-n-pages']),
            verbose=arguments['--verbose'])
        print(matrix)
        # df = DataFrame(matrix, index=rows, columns=columns)
        # print(df.to_csv(encoding='utf-8'))

    elif arguments['save-tfidf-vectorizer']:
        if arguments['--filename']:
            filename = arguments['--filename']
        else:
            filename = TFIDF_VECTORIZER_FILENAME

        texts = (page['text'] for page in dump_file.iter_article_pages(
            max_n_pages=max_n_pages,
            display=display))

        # Cannot unzip the iterator
        titles = [page['title']
                  for page in dump_file.iter_article_pages(
                          max_n_pages=max_n_pages,
                          display=display)]

        if display:
            tqdm.write('TFIDF vectorizing')
        transformer = TfidfVectorizer()
        transformer.fit(texts)
        transformer.rows = titles

        if display:
            tqdm.write('Writing tfidf vectorizer to {}'.format(filename))
        with codecs.open(filename, 'w', encoding='utf-8') as f:
            f.write(jsonpickle.encode(transformer))


if __name__ == '__main__':
    main()
