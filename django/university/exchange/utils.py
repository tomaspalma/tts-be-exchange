from datetime import date
from university.models import CourseMetadata, CourseUnit, DirectExchangeParticipants
from enum import Enum
import json
import requests

class ExchangeStatus(Enum):
    FETCH_SCHEDULE_ERROR = 1
    STUDENTS_NOT_ENROLLED = 2
    CLASSES_OVERLAP = 3
    SUCCESS = 4

def get_student_schedule_url(username, semana_ini, semana_fim):
    return f"https://sigarra.up.pt/feup/pt/mob_hor_geral.estudante?pv_codigo={username}&pv_semana_ini={semana_ini}&pv_semana_fim={semana_fim}" 

def build_new_schedules(student_schedules, exchanges, auth_username):
    for curr_exchange in exchanges:
        other_student = curr_exchange["other_student"]
        course_unit = curr_exchange["course_unit"]
        class_auth_student_goes_to = curr_exchange["old_class"]
        class_other_student_goes_to = curr_exchange["new_class"] # The other student goes to its new class
        
        # If participant is neither enrolled in that course unit or in that class
        other_student_valid = (class_auth_student_goes_to, course_unit) in student_schedules[other_student]
        auth_user_valid = (class_other_student_goes_to, course_unit) in student_schedules[auth_username]
        if not(other_student_valid) or not(auth_user_valid):
            return (ExchangeStatus.STUDENTS_NOT_ENROLLED, None)

        # Change schedule
        tmp = student_schedules[auth_username][(class_other_student_goes_to, course_unit)]
        student_schedules[auth_username][(class_auth_student_goes_to, course_unit)] = student_schedules[other_student][(class_auth_student_goes_to, course_unit)]
        student_schedules[other_student][(class_other_student_goes_to, course_unit)] = tmp

        del student_schedules[other_student][(class_auth_student_goes_to, course_unit)] # remove old class of other student
        del student_schedules[auth_username][(class_other_student_goes_to, course_unit)] # remove old class of auth student

    return (ExchangeStatus.SUCCESS, None)     

def build_student_schedule_dicts(student_schedules, exchanges, semana_ini, semana_fim, cookies):
    for curr_exchange in exchanges:
        curr_username = curr_exchange["other_student"]
        if not(curr_username in student_schedules):
            schedule_request = requests.get(get_student_schedule_url(curr_username, semana_ini, semana_fim), cookies=cookies)
            if(schedule_request.status_code != 200):
                return (ExchangeStatus.FETCH_SCHEDULE_ERROR, schedule_request.status_code)

            schedule = json.loads(schedule_request.content)["horario"]
            student_schedules[curr_username] = build_student_schedule_dict(schedule)

    return (ExchangeStatus.SUCCESS, None)

def check_for_overlaps(student_schedules, exchanges, inserted_exchanges, exchange_db_model, auth_user):
    if exchange_overlap(student_schedules, auth_user):
        return (ExchangeStatus.CLASSES_OVERLAP, None)

    for curr_exchange in exchanges:
        other_student = curr_exchange["other_student"]

        if exchange_overlap(student_schedules, other_student):
            return (ExchangeStatus.CLASSES_OVERLAP, None)
    
        inserted_exchanges.append(DirectExchangeParticipants(
            participant=curr_exchange["other_student"],
            old_class=curr_exchange["old_class"], 
            new_class=curr_exchange["new_class"],
            course_unit=curr_exchange["course_unit"],
            direct_exchange=exchange_db_model,
            accepted=False
        ))

        inserted_exchanges.append(DirectExchangeParticipants(
            participant=auth_user,
            old_class=curr_exchange["new_class"], # This is not a typo, the old class of the authenticted student is the new class of the other student
            new_class=curr_exchange["old_class"],
            course_unit=curr_exchange["course_unit"],
            direct_exchange=exchange_db_model,
            accepted=False
        ))

    return (ExchangeStatus.SUCCESS, None)


def build_student_schedule_dict(schedule: list):
    return {
        (class_schedule["turma_sigla"], class_schedule["ucurr_sigla"]): class_schedule for class_schedule in schedule if class_schedule["tipo"] == "TP"
    }

def check_class_schedule_overlap(day_1: int, start_1: int, end_1: int, day_2: int, start_2: int, end_2: int) -> bool:
    if day_1 != day_2:
        return False

    if (start_2 >= start_1 and start_2 <= end_1) or (start_1 >= start_2 and start_1 <= end_2):
        return True

    return False


def exchange_overlap(student_schedules, student) -> bool:
    for (key, class_schedule) in student_schedules[student].items():
        for (other_key, other_class_schedule) in student_schedules[student].items():
            if key == other_key:
                continue

            (class_schedule_day, class_schedule_start, class_schedule_end) = (class_schedule["dia"], class_schedule["hora_inicio"], class_schedule["aula_duracao"] + class_schedule["hora_inicio"])
            (overlap_param_day, overlap_param_start, overlap_param_end) = (other_class_schedule["dia"], other_class_schedule["hora_inicio"], other_class_schedule["aula_duracao"] + other_class_schedule["hora_inicio"])

            if check_class_schedule_overlap(class_schedule_day, class_schedule_start, class_schedule_end, overlap_param_day, overlap_param_start, overlap_param_end):
                return True

    return False

"""
    Returns name of course unit
"""
def course_unit_name(course_unit_id):
    course_unit = CourseUnit.objects.get(sigarra_id=course_unit_id)
    return course_unit.name

def curr_semester_weeks():
    currdate = date.today()
    year = str(currdate.year)
    primeiro_semestre = currdate.month >= 10 and currdate.month <= 12
    if primeiro_semestre: 
        semana_ini = "1001"
        semana_fim = "1201"
    else:
        semana_ini = "0101"
        semana_fim = "0601"
    return (year+semana_ini, year+semana_fim)

def append_tts_info_to_sigarra_schedule(schedule):
    course_unit = CourseUnit.objects.filter(sigarra_id=schedule['ocorrencia_id'])[0]
    course_metadata = CourseMetadata.objects.filter(course_unit=course_unit.id)[0]
            
    schedule['url'] = course_unit.url
    # The sigarra api does not return the course with the full name, just the acronym
    schedule['ucurr_nome'] = course_unit_name(schedule['ocorrencia_id'])

    schedule['ects'] = course_metadata.ects
    schedule['last_updated'] = course_unit.last_updated

