import re
import thread

import helpers.models as helpers
import os
import pandas as pd
import re
import time

from django.db import models
from django.db import transaction
from mptt.models import MPTTModel
from mptt.models import TreeForeignKey
from silk.profiling.profiler import silk_profile
from django.core.files.storage import FileSystemStorage
from django.conf import settings

cb_regex = '^cb_.*$'


class OverwriteStorage(FileSystemStorage):

    def get_available_name(self, name):
        """Returns a filename that's free on the target storage system, and
        available for new content to be written to.

        Found at http://djangosnippets.org/snippets/976/

        This file storage solves overwrite on upload problem. Another
        proposed solution was to override the save method on the model
        like so (from https://code.djangoproject.com/ticket/11663):

        def save(self, *args, **kwargs):
            try:
                this = MyModelName.objects.get(id=self.id)
                if this.MyImageFieldName != self.MyImageFieldName:
                    this.MyImageFieldName.delete()
            except: pass
            super(MyModelName, self).save(*args, **kwargs)
        """
        # If the filename already exists, remove it as if it was a true file system
        if self.exists(name):
            os.remove(os.path.join(settings.MEDIA_ROOT, name))
        return name


class Job(MPTTModel, helpers.TimeMixin):
    name = models.CharField(max_length=255)
    number = models.IntegerField()
    parent = TreeForeignKey('self', null=True, blank=True,
                            related_name='children_jobs')

    def __str__(self):
        return '{}_{}'.format(self.number, self.name)


class Question(MPTTModel, helpers.TimeMixin):
    name = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)
    title = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=255)
    parent = TreeForeignKey(Job, null=True, blank=True,
                            related_name='children_questions')
    code_book = models.ForeignKey('CodeBook', null=True, blank=True)  # key for root code

    def __str__(self):
        return '{}({})'.format(self.title, self.kind)


class CodeBook(helpers.TimeMixin):
    name = models.CharField(max_length=255)
    job = models.ForeignKey('Job')

    def __str__(self):
        return '{}'.format(self.name)


class Code(MPTTModel, helpers.TimeMixin):
    ''' codes, nets and books in one model
    '''
    text = models.TextField(blank=True, null=True)
    title = models.CharField(max_length=255, blank=True)
    code = models.IntegerField(blank=True, null=True)
    parent = TreeForeignKey('self', null=True, blank=True,
                            related_name='children_codes')
    code_book = models.ForeignKey('CodeBook', null=True, blank=True,
                                  related_name='children_codes')
    job = models.ForeignKey('Job')
    overcode = models.BooleanField()

    def __str__(self):
        return '{}'.format(self.title)


class Verbatim(MPTTModel, helpers.TimeMixin):
    question = models.ForeignKey('Question', blank=True, null=True)
    variable = models.ForeignKey('Variable', blank=True, null=True)
    verbatim = models.TextField()
    parent = TreeForeignKey('Code', null=True, blank=True,
                            related_name='children_verbatims')
    job = models.ForeignKey('Job')

    def __str__(self):
        return '{}'.format(self.verbatim)


class Variable(helpers.TimeMixin):
    uid = models.IntegerField()
    sex = models.IntegerField(blank=True, null=True)
    age_bands = models.IntegerField(blank=True, null=True)
    reg_quota = models.IntegerField(blank=True, null=True)
    csp_quota = models.IntegerField(blank=True, null=True)
    main_cell_text = models.CharField(max_length=255, blank=True, null=True)
    job = models.ForeignKey('Job')

    def __str__(self):
        return '[{}][{}][{}][{}][{}][{}]'.format(self.uid,
                                                 self.sex,
                                                 self.age_bands,
                                                 self.reg_quota,
                                                 self.csp_quota,
                                                 self.main_cell_text)


class UploadFile(helpers.TimeMixin):
    """Upload and process template file.

    Parses and uploads data from .xls spreadsheets to the existing databases
    Each file represents single job

    Attributes:
    -----------
        job : Job
            temporary attribute. Represents job entity while input's processing
        code_books_entities : dict
            dict {code_book_name : code_book_entity}, contains codebooks, available
            in current file
    """

    # TODO: Comment and split to smaller function
    # TODO: improve performace, bulk DB write

    sheet = models.FileField(storage=OverwriteStorage(),
                             upload_to=lambda i, x: x)

    def __str__(self):
        return '{}'.format(os.path.basename(self.sheet.name))

    def parse_questions(self, excel_file):
        """Separate method to parse questions from spreadsheet

        Arguments:
        ----------
            excel_file: ExcelFile
                input .xls file, that contains data about questions
        """
        start = time.clock()
        question_df = excel_file.parse('question')
        question_df = question_df.where(pd.notnull(question_df), None)
        q_to_save = list()
        try:
            q_id = Question.objects.latest('id').id + 1
        except:
            q_id = 0

        questions_in_df = dict()

        questions_names = list()
        questions_kinds = list()
        questions_codebooks = list()

        for question in question_df.iterrows():
            qq = question[1]
            if qq.code_book not in questions_codebooks:
                questions_codebooks.append(qq.code_book)
            questions_names.append(qq.id)
            questions_kinds.append(qq.type)
            q = {
                'id': q_id,
                'name': qq.id,
                'kind': qq.type,
                'parent': self.job,
                'text_en': qq.text,
                'title_en': qq.title
            }
            for key in qq.keys():
                if key.count('text_') or key.count('title_'):
                    q[key] = qq[key]

            q_id += 1

            questions_in_df[(qq.id, qq.type, self.job, qq.code_book)] = q

        # TODO: MAKE INDEX
        existing_codebooks = {cb.name: cb for cb in CodeBook.objects
                                .filter(name__in=questions_codebooks)}

        # TODO: OPTIMIZE QUERY
        existing_questions = {(q.name, q.kind, q.parent, q.code_book): q
                              for q in Question.objects.filter(
            name__in=questions_names,
            parent=self.job,
            kind__in=questions_kinds,
            code_book__in=existing_codebooks.values()
        )}

        for key, q in questions_in_df.iteritems():
            if key[3] in existing_codebooks.keys():
                cb = existing_codebooks[key[3]]
            else:
                cb = CodeBook.objects.create(job=self.job, name=key.code_book)
                existing_codebooks[cb.name] = cb

            key = (key[0], key[1], key[2], cb)

            if key in existing_questions.keys():
                question = existing_questions[key]
            else:
                question = Question()

            for attribute, value in q.iteritems():
                setattr(question, attribute, value)
            question.code_book = cb
            q_to_save.append(question)
        # TODO: USE BULK CRAEATE AND Q_ID
        for q in q_to_save:
            q.save()
        return q_to_save

    def parse_variables(self, excel_file):
        """Method that parses variables from spreadsheet

        Arguments:
        ----------
            excel_file: ExcelFile
                input .xls file, that contains data about variables
        """

        start = time.clock()
        variable_df = excel_file.parse('variables')
        variable_df = variable_df.where(pd.notnull(variable_df), None)
        variable_records = set()

        try:
            v_id = Variable.objects.latest('id').id + 1
        except:
            v_id = 0

        for vverbatim in variable_df.iterrows():
            verbat = vverbatim[1]
            v = Variable(
                id=v_id,
                uid=verbat[0],
                sex=verbat[1],
                age_bands=verbat[2],
                reg_quota=verbat[3],
                csp_quota=verbat[4],
                main_cell_text=verbat[5],
                job=self.job
            )
            variable_records.add(v)

            v_id += 1

        # TODO: BULK CREATE
        for v in variable_records:
            v.save()
        return variable_records

    def parse_codes(self, excel_file, code_books):
        """Method that parses codes from spreadsheet

        Arguments:
        ----------
            excel_file: ExcelFile
                input .xls file, that contains data about codes
        """

        start = time.clock()
        for key, cb in self.code_books_entities.iteritems():
            codes_dict = dict()
            parents_dict = dict()
            related_codes = dict()

            codes_text = list()
            codes_titles = list()
            codes_nets = list()
            codes_codebooks = list()

            try:
                code_book = cb
            except Exception as e:
                print key, self.job
                raise e
            cb = code_books[key]
            cb = cb.where(pd.notnull(cb), None)
            ocb = cb[pd.isnull(cb.Codes)]  # overcodes
            cb = cb[pd.notnull(cb.Codes)]

            for oocode in ocb.iterrows():
                code = oocode[1]
                codes_text.append(code.text)
                codes_titles.append(code.title)
                codes_nets.append(code.NET)
                codes_codebooks.append(code_book)
                ccode = {
                    'text': code.text,
                    'title': code.title,
                    'code': code.NET,
                    'parent': None,
                    'code_book': code_book,
                    'job': self.job,
                    'overcode': True,
                    'text_en': code.text,
                    'title_en': code.title
                }
                for ckey in code.keys():
                    if ckey.count('text_') > 0 or ckey.count('title_') > 0:
                        ccode[ckey] = code[ckey]
                # oc.save()
                codes_dict[(ccode['code'], ccode['text'], ccode['title'])] = ccode
            # TODO: OPTIMIZE QUERY
            existing_codes = {(code.text, code.title,):
                                  code for code in Code.objects.filter(text__in=codes_text, title__in=codes_titles,
                                                                       code__in=codes_nets, code_book=code_book,
                                                                       job=self.job, parent=None)}
            for k, value in codes_dict.iteritems():
                if key in existing_codes.keys():
                    code = existing_codes[k]
                else:
                    code = Code()
                for arg, val in value.iteritems():
                    setattr(code, arg, val)
                code.save()

            parent_nets = list()
            related_nets = list()
            related_text = list()
            related_titles = list()

            codes_parent_nets = list()
            for ccode in cb.iterrows():
                code = ccode[1]
                parent_nets.append(code.NET)
                codes_parent_nets.append(code.NET)
                parent = {
                    'code': code.NET,
                    'job': self.job,
                    'overcode': True,
                    'code_book': code_book,
                    'parent': None
                }

                parent_key = parent['code']
                if parent not in parents_dict.values():
                    parents_dict[parent_key] = parent

                related_text.append(code.text)
                related_titles.append(code.title)
                related_nets.append(code.Codes)
                c = {
                    'text': code.text,
                    'title': code.title,
                    'code': code.Codes,
                    'job': self.job,
                    'overcode': False,
                    'parent': parent_key,
                    'code_book': code_book,
                    'text_en': code.text,
                    'title_en': code.title
                }

                for ckey in code.keys():
                    if ckey.count('text_') > 0 or ckey.count('title_') > 0:
                        c[ckey] = code[ckey]

                related_codes[(c['text'], c['title'], c['code'], parent_key)] = c

            existing_parents = {code.code: code for code in Code.objects.filter(code__in=codes_parent_nets,
                                                                                job=self.job,
                                                                                overcode=True,
                                                                                code_book=code_book,
                                                                                parent=None)}
            existing_related = {(code.text, code.title, code.NET, code.parent): code
                                for code in Code.objects.filter(code__in=related_nets,
                                                                job=self.job,
                                                                overcode=False,
                                                                parent__in=existing_parents,
                                                                text__in=related_text,
                                                                title__in=related_titles)}

            for key, value in related_codes.iteritems():
                if key[3] in existing_parents.keys():
                    key = (key[0], key[1], key[2], existing_parents[key[3]])
                else:
                    parent = Code()
                    for arg, val in parents_dict[value['parent']].iteritems():
                        setattr(parent, arg, val)
                    parent.save()
                    key = (key[0], key[1], key[2], parent)
                    existing_parents[parent.code] = parent

                value['parent'] = key[3]

                if key in existing_related.keys():
                    code = existing_related[key]
                else:
                    code = Code()

                for arg, val in value.iteritems():
                    setattr(code, arg, val)

                code.save()

    def parse_code_books(self, excel_file):
        code_books_names = filter(lambda x: re.match(cb_regex, x, re.IGNORECASE) is not None, excel_file.sheet_names)
        code_books = {name: excel_file.parse(name) for name in code_books_names}
        return {name: CodeBook.objects.get_or_create(job=self.job, name=name)[0] for name in
                code_books_names}, code_books

    def parse_verbatims(self, excel_file):
        """Method that parses verbatims and adds it to database

        Current parsing method ignores if input file contains verbatims
        that refers to unexisting question and has unexisting codes

        Arguments:
        ----------
            excel_file: ExcelFile
                input .xls file, that contains data about codes
        """
        verbatim_df = excel_file.parse('verbatims')
        verbatim_df = verbatim_df.where(pd.notnull(verbatim_df), None)

        keys = verbatim_df.keys()
        var_id = keys[0]
        q = set()
        questions = {q.name: q for q in Question.objects.filter(parent=self.job)}
        codes = {(code.code, code.code_book): code for code in Code.objects.filter(job=self.job)}
        ver_to_save = list()

        for k in keys[1:]:
            q.add(k.split('-')[0].rstrip())

        vars = {var.uid: var for var in Variable.objects.filter(job=self.job)}
        # for each question in the top row
        for q_n in q:
            start = time.clock()

            # Respondents serial
            var_keys = [var_id, ]
            for vk in keys:
                if vk.count(q_n) > 0:
                    var_keys.append(vk)
            vdf = verbatim_df[var_keys]
            question = questions[q_n]

            '''
            Is there any logic to create codes with no enough information about it?
            Is there a reason to save an verbatim of unknovn variable?
            '''
            for vverbatim in vdf.iterrows():
                verbat = vverbatim[1]
                if verbat[0] is not None:
                    var = vars[verbat[0]]
                else:
                    continue
                text = verbat[1]
                if text is None:
                    continue  # TODO: dropna for df
                for code_id in verbat[2:]:
                    if code_id is None:
                        continue
                    code = codes[(code_id, question.code_book)]

                    if code is None:
                        continue

                    v = Verbatim(
                        job=self.job,
                        variable=var,
                        verbatim=text,
                        parent=code,
                        question=question
                    )
                    v.verbatim_en = text
                    for ckey in verbat.keys():
                        if ckey.count('text_') > 0:
                            lang = ckey.split('_')[-1]
                            setattr(v, 'verbatim_' + lang, verbat[ckey])
                    v.save()


    @transaction.atomic
    @silk_profile(name='Save File')
    def save(self, force_insert=False, force_update=False, using=None,
             update_fields=None):

        file_name = self.sheet.path
        fname = self.sheet.path

        file_name = os.path.splitext(file_name)[0]
        file_name = os.path.basename(file_name)

        job_number = file_name.split('_')[0]
        job_name = ' '.join(file_name.split('_')[1:])

        job = Job.objects.get_or_create(name=os.path.basename(job_name), number=job_number)
        if job[1]:  # exists
            job[0].delete()
        job = job[0]

        super(UploadFile, self).save(force_insert=force_insert, force_update=force_update,
                                     using=using, update_fields=update_fields)

        f = pd.ExcelFile(fname)

        job = Job.objects.create(name=job_name, number=job_number)

        self.job = job
        self.code_books_entities, code_books = self.parse_code_books(f)
        self.parse_questions(f)
        self.parse_variables(f)
        self.parse_codes(f, code_books)
        self.parse_verbatims(f)

