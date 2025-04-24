from celery import shared_task
from .models import Scenario, Phase, Activity, UserAnswer, Answer, QuestionBunch, ActivityType
from django.core.cache import cache
from datetime import timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.contrib.auth.models import User
from usergroups.models import UserGroupMembership
from django.utils.dateparse import parse_date
from django.db.models import Sum, Count, Q, Max, Min
from collections import defaultdict
from .utils import get_last_answers, get_first_answers
import os, io
import csv
from django.utils.timezone import now

@shared_task
def compute_sankey_data(scenario_id, group_ids, start_date, end_date):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    start_date = start_date
    end_date = end_date
    
    phases = Phase.objects.filter(scenario=scenario).order_by('id')
    
    nodes = []
    links = []
    phase_performance = {phase.id: {'High': 0, 'Moderate': 0, 'Low': 0} for phase in phases}

    # Get the last answers (only the latest answer for each user/activity combination)
    last_answers = get_last_answers(scenario_id)

    # 25 FEB
    if group_ids:
        group_ids = [int(g) for g in group_ids.split(',')]  # Convert string IDs to list of integers
        users = User.objects.filter(
            id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
        ).distinct()# .exclude(groups__name='teachers')
    else:
        users = User.objects.filter(
            Q(userscenarioscore__scenario=scenario) & 
            (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
            ).distinct()
    
    users = users.exclude(groups__name='teachers')

    if start_date:
        start_date = parse_date(start_date)
        if start_date:
            last_answers = last_answers.filter(created_on__gte=start_date)
    
    if end_date:
        end_date = parse_date(end_date)
        if end_date:
            last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))

    # Get the minimum activity ID in the scenario
    min_activity_id = Activity.objects.filter(scenario=scenario).order_by('id').first()

    if not min_activity_id:
        return JsonResponse({"error": "No activities found for this scenario"}, status=400)
    
    valid_users = []  # List of users who started with the minimum activity

    for user in users:
        # Check if user has answered the minimum activity
        if last_answers.filter(user=user, activity=min_activity_id).exists():
            valid_users.append(user)

    user_performance = {user.id: [] for user in valid_users}
    
    for user in valid_users:
        for phase in phases:
            activities = Activity.objects.filter(phase=phase)
            total_primary_score = 0
            total_primary_max_score = 0
            processed_activities = set()  # To avoid duplicates

            # Handle primary evaluatable activities (only)
            primary_evaluatable_activities = activities.filter(is_evaluatable=True, is_primary_ev=True)
            primary_count = primary_evaluatable_activities.count()

            if primary_count > 0:
                primary_weight_share = 100 / primary_count  # Each primary activity contributes equally

                for primary_activity in primary_evaluatable_activities:
                    try:
                        question_bunch = QuestionBunch.objects.get(activity_primary=primary_activity)
                        bunch_activities = Activity.objects.filter(id__in=question_bunch.activity_ids)
                    except QuestionBunch.DoesNotExist:
                        bunch_activities = [primary_activity]  # Fallback to the primary activity alone

                    for bunch_activity in bunch_activities:
                        if bunch_activity.id not in processed_activities:
                            user_last_answer = last_answers.filter(user=user, activity=bunch_activity).first()
                            if user_last_answer and user_last_answer.answer:
                                total_primary_score += (user_last_answer.answer.answer_weight * primary_weight_share) / 100

                            # Calculate max score for each bunch activity
                            highest_answer_weight = Answer.objects.filter(activity=bunch_activity).order_by('-answer_weight').first()
                            if highest_answer_weight:
                                total_primary_max_score += (highest_answer_weight.answer_weight * primary_weight_share) / 100

                            # Mark bunch activity as processed
                            processed_activities.add(bunch_activity.id)

            if total_primary_max_score > 0:
                percentage_score = (total_primary_score / total_primary_max_score) * 100

                # Determine the performance category
                if percentage_score >= 83.3:
                    phase_performance[phase.id]['High'] += 1
                    user_performance[user.id].append('High')
                elif percentage_score >= 49.7:
                    phase_performance[phase.id]['Moderate'] += 1
                    user_performance[user.id].append('Moderate')
                else:
                    phase_performance[phase.id]['Low'] += 1
                    user_performance[user.id].append('Low')
            else:
                user_performance[user.id].append('None')

    # Prepare nodes
    for phase in phases:
        nodes.append({'name': f'High Performers {phase.name}', 'position': 1})
        nodes.append({'name': f'Moderate Performers {phase.name}', 'position': 2})
        nodes.append({'name': f'Low Performers {phase.name}', 'position': 3})

    # Prepare links
    transition_counts = {}
    for i in range(len(phases) - 1):
        current_phase = phases[i]
        next_phase = phases[i + 1]
        transition_counts[(current_phase.id, next_phase.id)] = {
            'High-High': 0, 'High-Moderate': 0, 'High-Low': 0,
            'Moderate-High': 0, 'Moderate-Moderate': 0, 'Moderate-Low': 0,
            'Low-High': 0, 'Low-Moderate': 0, 'Low-Low': 0
        }

        for user in valid_users:
            current_performance = user_performance[user.id][i]
            next_performance = user_performance[user.id][i + 1]

            if current_performance != 'None' and next_performance != 'None':
                transition_counts[(current_phase.id, next_phase.id)][f'{current_performance}-{next_performance}'] += 1

    for (current_phase_id, next_phase_id), counts in transition_counts.items():
        current_phase = Phase.objects.get(id=current_phase_id)
        next_phase = Phase.objects.get(id=next_phase_id)

        for transition, count in counts.items():
            if count > 0:
                source_performance, target_performance = transition.split('-')
                source_name = f'{source_performance} Performers {current_phase.name}'
                target_name = f'{target_performance} Performers {next_phase.name}'
                if source_name in [node['name'] for node in nodes] and target_name in [node['name'] for node in nodes]:
                    links.append({
                        'source': source_name,
                        'target': target_name,
                        'value': count
                    })

    # Filter nodes to only include those with links
    linked_nodes = set()
    for link in links:
        linked_nodes.add(link['source'])
        linked_nodes.add(link['target'])

    nodes = [node for node in nodes if node['name'] in linked_nodes]
    data = {'nodes': nodes, 'links': links}

    return data

@shared_task
def compute_final_performance(scenario_id, group_ids, start_date, end_date):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    start_date = start_date
    end_date = end_date
        
    phases = Phase.objects.filter(scenario=scenario).order_by('id')
    
    # Define the weights for each phase in the order they come
    phase_weights = [0.2, 0.2, 0.45, 0.15]
    if len(phases) > 4:
        phase_weights = [0.2, 0.2, 0.3, 0.15, 0.15]

    # Get the last answers (only the latest answer for each user/activity combination)
    last_answers = get_last_answers(scenario_id)
    
    # FEB 28
    if group_ids:
        users = User.objects.filter(
            id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
        ).distinct()# .exclude(groups__name='teachers')
    else:
        users = User.objects.filter(
            Q(userscenarioscore__scenario=scenario) & 
            (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
            ).distinct()
    
    users = users.exclude(groups__name='teachers')
    
    # Apply start_date and end_date filters
    if start_date:
        start_date = parse_date(start_date)
        if start_date:
            last_answers = last_answers.filter(created_on__gte=start_date)
    
    if end_date:
        end_date = parse_date(end_date)
        if end_date:
            last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))

    performance_counts = {'High': 0, 'Moderate': 0, 'Low': 0}
    user_performance = {user.id: {'weighted_score': 0, 'max_weighted_score': 0, 'phases_completed': 0} for user in users}

    # Get the minimum activity ID in the scenario
    min_activity_id = Activity.objects.filter(scenario=scenario).order_by('id').first()

    if not min_activity_id:
        return JsonResponse({"error": "No activities found for this scenario"}, status=400)
    
    valid_users = []  # List of users who started with the minimum activity

    for user in users:
        # Check if user has answered the minimum activity
        if last_answers.filter(user=user, activity=min_activity_id).exists():
            valid_users.append(user)

    for user in valid_users:
        for index, phase in enumerate(phases):
            primary_total_score = 0
            primary_max_score = 0
            processed_activities = set()

            # Handle primary evaluatable activities
            primary_evaluatable_activities = Activity.objects.filter(phase=phase, is_evaluatable=True, is_primary_ev=True)
            primary_count = primary_evaluatable_activities.count()

            if primary_count > 0:
                primary_weight_share = 100 / primary_count  # Each primary activity contributes equally

                for primary_activity in primary_evaluatable_activities:
                    try:
                        question_bunch = QuestionBunch.objects.get(activity_primary=primary_activity)
                        bunch_activities = Activity.objects.filter(id__in=question_bunch.activity_ids)
                    except QuestionBunch.DoesNotExist:
                        bunch_activities = [primary_activity]  # Fallback to the primary activity alone

                    for bunch_activity in bunch_activities:
                        if bunch_activity.id not in processed_activities:
                            user_last_answer = last_answers.filter(user=user, activity=bunch_activity).first()
                            if user_last_answer and user_last_answer.answer:
                                primary_total_score += (user_last_answer.answer.answer_weight * primary_weight_share) / 100

                            # Calculate max score for each bunch activity
                            highest_answer_weight = Answer.objects.filter(activity=bunch_activity).order_by('-answer_weight').first()
                            if highest_answer_weight:
                                primary_max_score += (highest_answer_weight.answer_weight * primary_weight_share) / 100

                            # Mark the bunch activity as processed
                            processed_activities.add(bunch_activity.id)

            # Calculate weighted scores using the corresponding phase weight
            if primary_max_score > 0:
                weight = phase_weights[index]  # Get the weight based on phase order
                weighted_score = (primary_total_score / primary_max_score) * weight
                max_weighted_score = weight
                user_performance[user.id]['weighted_score'] += weighted_score
                user_performance[user.id]['max_weighted_score'] += max_weighted_score
                user_performance[user.id]['phases_completed'] += 1  # Track completed phases
    
    total_students = len(valid_users)
    waterfall_data = [{'name': 'Total', 'value': total_students}]
    
    for user in valid_users:
        user_data = user_performance[user.id]
        if user_data['max_weighted_score'] > 0 and user_data['phases_completed'] > 0:
            percentage_score = (user_data['weighted_score'] / user_data['max_weighted_score']) * 100
            if percentage_score >= 83.3:
                performance_category = 'High'
            elif percentage_score >= 49.7:
                performance_category = 'Moderate'
            else:
                performance_category = 'Low'

            performance_counts[performance_category] += 1
    
    waterfall_data.append({'name': 'High', 'value': performance_counts['High']})
    waterfall_data.append({'name': 'Moderate', 'value': performance_counts['Moderate']})
    waterfall_data.append({'name': 'Low', 'value': performance_counts['Low']})
    
    data = {'waterfall_data': waterfall_data}

    return data

@shared_task
def compute_activity_answers_data(scenario_id, group_ids, start_date, end_date, activity_type):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    data_type = activity_type
    start_date = start_date
    end_date = end_date
    
    # Validate and parse dates
    if start_date:
        start_date = parse_date(str(start_date))
    if end_date:
        end_date = parse_date(str(end_date))

    data = {
        'categories': [],
        'correct': [],
        'incorrect': []
    }

    # Get the last answers (only the latest answer for each user/activity combination)
    last_answers = get_last_answers(scenario_id)
    
    # Filter users based on your criteria
    if group_ids:
        users = User.objects.filter(
            id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
        ).distinct().exclude(groups__name='teachers')
    else:
        users = User.objects.filter(
            Q(userscenarioscore__scenario=scenario) & 
            (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
            ).distinct().exclude(groups__name='teachers')

    # Get the IDs of the filtered users
    user_ids = users.values_list('id', flat=True)

    # Filter `last_answers` to include only entries from the specified users
    filtered_last_answers = last_answers.filter(user_id__in=user_ids)

    # Get the minimum activity ID in the scenario
    min_activity = Activity.objects.filter(scenario=scenario).order_by('id').first()

    if not min_activity:
        return JsonResponse({"error": "No activities found for this scenario"}, status=400)

    # List of valid user IDs who started with the minimum activity
    valid_user_ids = []

    for user in users:
        # Check if the user has answered the minimum activity
        if filtered_last_answers.filter(user=user, activity=min_activity).exists():
            valid_user_ids.append(user.id)

    # Now filter user_ids to include only those in valid_user_ids
    user_ids = [user_id for user_id in user_ids if user_id in valid_user_ids]

    # Filter `filtered_last_answers` to include only answers from valid users
    last_answers = filtered_last_answers.filter(user_id__in=user_ids) # filtered_last_answers

    # Apply start_date and end_date filters to last answers
    if start_date:
        last_answers = last_answers.filter(created_on__gte=start_date)
    if end_date:
        last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))

    if data_type == 'activities':
        # Handle activities as in the previous logic
        activity_type = get_object_or_404(ActivityType, name='Question')
        activities = Activity.objects.filter(scenario=scenario, activity_type=activity_type)

        for activity in activities:
            data['categories'].append(activity.name)

            # Filter last answers for this specific activity
            activity_last_answers = last_answers.filter(activity=activity)

            # Separate correct and incorrect answers based on the last answer
            correct_answers = activity_last_answers.filter(answer__is_correct=True)
            incorrect_answers = activity_last_answers.filter(answer__is_correct=False)

            # Get distinct user IDs for correct and incorrect answers
            correct_user_ids = correct_answers.values_list('user_id', flat=True).distinct()
            incorrect_user_ids = incorrect_answers.exclude(user_id__in=correct_user_ids).values_list('user_id', flat=True).distinct()

            data['correct'].append(len(correct_user_ids))
            data['incorrect'].append(len(incorrect_user_ids))
            
    else:
        # Now we're focusing on phases
        phases = Phase.objects.filter(scenario=scenario).order_by('id')

        for phase in phases:
            data['categories'].append(phase.name)

            # Initialize counters for the current phase
            total_correct_in_phase = 0
            total_incorrect_in_phase = 0

            # Fetch activities for the phase
            activities = Activity.objects.filter(phase=phase)

            for activity in activities:
                # Filter last answers for each activity in the phase
                activity_last_answers = last_answers.filter(activity=activity)

                # Separate correct and incorrect answers
                correct_answers = activity_last_answers.filter(answer__is_correct=True)
                incorrect_answers = activity_last_answers.filter(answer__is_correct=False)

                # Get distinct user IDs for correct and incorrect answers
                correct_user_ids = list(correct_answers.values_list('user_id', flat=True).distinct())
                incorrect_user_ids = list(incorrect_answers.values_list('user_id', flat=True).distinct())

                # Remove correct user IDs from incorrect user IDs
                incorrect_user_ids = [uid for uid in incorrect_user_ids if uid not in correct_user_ids]

                # Add the counts to the phase totals
                total_correct_in_phase += len(correct_user_ids)
                total_incorrect_in_phase += len(incorrect_user_ids)

            # Append the summed counts for the entire phase
            data['correct'].append(total_correct_in_phase)
            data['incorrect'].append(total_incorrect_in_phase)

    return data

@shared_task
def compute_performance_data(scenario_id, group_ids, start_date, end_date):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    start_date = start_date
    end_date = end_date
    
    phases = Phase.objects.filter(scenario=scenario)

    phase_performance = {phase.name: {'High': 0, 'Mid': 0, 'Low': 0} for phase in phases}
    phase_total_users = {phase.name: 0 for phase in phases}

    # Get the last answers (only the latest answer for each user/activity combination)
    last_answers = get_last_answers(scenario_id)

    # Apply start_date and end_date filters
    if start_date:
        start_date = parse_date(start_date)
        if start_date:
            last_answers = last_answers.filter(created_on__gte=start_date)
    
    if end_date:
        end_date = parse_date(end_date)
        if end_date:
            last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))

    # Get the minimum activity ID in the scenario
    min_activity_id = Activity.objects.filter(scenario=scenario).order_by('id').first()

    if not min_activity_id:
        return JsonResponse({"error": "No activities found for this scenario"}, status=400)
    
    if group_ids:
        users = User.objects.filter(
            id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
        ).distinct()# .exclude(groups__name='teachers')
    else:
        users = User.objects.filter(
            Q(userscenarioscore__scenario=scenario) & 
            (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
            ).distinct()
    
    users = users.exclude(groups__name='teachers')

    valid_users = []  # List of users who started with the minimum activity

    for user in users:
        # Check if user has answered the minimum activity
        if last_answers.filter(user=user, activity=min_activity_id).exists():
            valid_users.append(user)

    for user in valid_users:
        for phase in phases:
            activities = Activity.objects.filter(phase=phase)
            total_primary_score = 0
            total_primary_max_score = 0
            processed_activities = set()  # To avoid duplicates

            # Handle only primary evaluatable activities (and their bunches)
            primary_evaluatable_activities = activities.filter(is_evaluatable=True, is_primary_ev=True)
            primary_count = primary_evaluatable_activities.count()

            if primary_count > 0:
                primary_weight_share = 100 / primary_count  # Each primary activity contributes equally

                for primary_activity in primary_evaluatable_activities:
                    try:
                        question_bunch = QuestionBunch.objects.get(activity_primary=primary_activity)
                        bunch_activities = Activity.objects.filter(id__in=question_bunch.activity_ids)
                    except QuestionBunch.DoesNotExist:
                        bunch_activities = [primary_activity]  # Fallback to the primary activity alone

                    for bunch_activity in bunch_activities:
                        if bunch_activity.id not in processed_activities:
                            user_last_answer = last_answers.filter(user=user, activity=bunch_activity).first()
                            if user_last_answer and user_last_answer.answer:
                                total_primary_score += (user_last_answer.answer.answer_weight * primary_weight_share) / 100

                            # Calculate max score for each bunch activity
                            highest_answer_weight = Answer.objects.filter(activity=bunch_activity).order_by('-answer_weight').first()
                            if highest_answer_weight:
                                total_primary_max_score += (highest_answer_weight.answer_weight * primary_weight_share) / 100

                            # Mark bunch activity as processed
                            processed_activities.add(bunch_activity.id)

            # Calculate performance based on total primary score
            if total_primary_max_score > 0:
                percentage_score = (total_primary_score / total_primary_max_score) * 100

                # Apply performance thresholds for primary evaluatable activities
                if percentage_score >= 83.3:
                    phase_performance[phase.name]['High'] += 1
                elif percentage_score >= 49.7:
                    phase_performance[phase.name]['Mid'] += 1
                else:
                    phase_performance[phase.name]['Low'] += 1

                phase_total_users[phase.name] += 1

    # Normalize the performance percentages by total users
    for phase in phases:
        total_users = phase_total_users[phase.name]
        if total_users > 0:
            phase_performance[phase.name]['High'] = (phase_performance[phase.name]['High'] / total_users) * 100
            phase_performance[phase.name]['Mid'] = (phase_performance[phase.name]['Mid'] / total_users) * 100
            phase_performance[phase.name]['Low'] = (phase_performance[phase.name]['Low'] / total_users) * 100

    return phase_performance

@shared_task
def compute_time_spent_data(scenario_id, group_ids, start_date, end_date, activity_type):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    data_type = activity_type
    start_date = start_date
    end_date = end_date
    
    # Validate and parse dates
    if start_date:
        start_date = parse_date(start_date) if isinstance(start_date, str) else None
    if end_date:
        end_date = parse_date(end_date) if isinstance(end_date, str) else None
    
    activity_type_q = get_object_or_404(ActivityType, name='Question')
    
    # Get the last answers (only the latest answer for each user/activity combination)
    last_answers = get_last_answers(scenario_id)

    # Apply start_date and end_date filters
    if start_date:
        last_answers = last_answers.filter(created_on__gte=start_date)
    
    if end_date:
        last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))

    data = {
        'categories': [],
        'time_spent': []
    }

    if data_type == 'activities_timing':
        activities = Activity.objects.filter(scenario=scenario, activity_type=activity_type_q)
        for activity in activities:
            data['categories'].append(activity.name)
            if group_ids:
                user_answers = last_answers.filter(
                    activity=activity,
                    user__id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
                ).distinct()
            else:
                user_answers = last_answers.filter(
                    activity=activity
                ).filter(
                    Q(user__school_department__isnull=False) |
                    Q(user__id__in=UserGroupMembership.objects.values('user_id'))
                ).distinct()
            user_answers = user_answers.exclude(user__groups__name='teachers')
            total_time = user_answers.aggregate(total=Sum('timing'))['total'] or 0
            count_answers = user_answers.values('user').distinct().count() or 1
            average_time = total_time / count_answers
            data['time_spent'].append(average_time)
    else:
        phases = Phase.objects.filter(scenario=scenario)
        for phase in phases:
            data['categories'].append(phase.name)
            if group_ids:
                user_answers = last_answers.filter(
                    activity__phase=phase,
                    user__id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
                ).distinct()
            else:
                user_answers = last_answers.filter(
                    activity__phase=phase
                ).filter(
                    Q(user__school_department__isnull=False) |
                    Q(user__id__in=UserGroupMembership.objects.values('user_id'))
                ).distinct()
            user_answers = user_answers.exclude(user__groups__name='teachers')
            total_time = user_answers.aggregate(total=Sum('timing'))['total'] or 0
            count_answers = user_answers.values('user').distinct().count() or 1
            average_time = total_time / count_answers
            data['time_spent'].append(average_time)

    return data

@shared_task
def compute_detailed_phase_scores_data(scenario_id, group_ids, start_date, end_date):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    start_date = start_date
    end_date = end_date

    # Get the last answers (only the latest answer for each user/activity combination)
    last_answers = get_last_answers(scenario_id)
    
    # Apply start_date and end_date filters
    if start_date:
        start_date = parse_date(start_date)
        last_answers = last_answers.filter(created_on__gte=start_date)
    
    if end_date:
        end_date = parse_date(end_date)
        last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))
    
    # Get the minimum activity ID in the scenario
    min_activity_id = Activity.objects.filter(scenario=scenario).order_by('id').first()

    if not min_activity_id:
        return JsonResponse({"error": "No activities found for this scenario"}, status=400)

    if group_ids:
        users = User.objects.filter(
            id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
        ).distinct()# .exclude(groups__name='teachers')
    else:
        users = User.objects.filter(
            Q(userscenarioscore__scenario=scenario) & 
            (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
            ).distinct()
    
    users = users.exclude(groups__name='teachers')

    valid_users = []  # List of users who started with the minimum activity

    for user in users:
        # Check if user has answered the minimum activity
        if last_answers.filter(user=user, activity=min_activity_id).exists():
            valid_users.append(user)
    
    phases = Phase.objects.filter(scenario=scenario)

    phase_scores = {phase.name: {'low': 0, 'mid': 0, 'high': 0, 'low_score': 0, 'mid_score': 0, 'high_score': 0, 'total_users': 0} for phase in phases}

    for user in valid_users:
        for phase in phases:
            total_primary_score = 0
            total_primary_max_score = 0
            processed_activities = set()

            activities_in_phase = Activity.objects.filter(phase=phase)

            # Process primary evaluatable activities (and their bunches)
            primary_evaluatable_activities = activities_in_phase.filter(is_evaluatable=True, is_primary_ev=True)
            primary_count = primary_evaluatable_activities.count()

            if primary_count > 0:
                primary_weight_share = 100 / primary_count  # Each primary activity contributes equally

                for primary_activity in primary_evaluatable_activities:
                    try:
                        question_bunch = QuestionBunch.objects.get(activity_primary=primary_activity)
                        bunch_activities = Activity.objects.filter(id__in=question_bunch.activity_ids)
                    except QuestionBunch.DoesNotExist:
                        bunch_activities = [primary_activity]  # Fallback to primary activity alone

                    for bunch_activity in bunch_activities:
                        if bunch_activity.id not in processed_activities:
                            user_last_answer = last_answers.filter(user=user, activity=bunch_activity).first()
                            if user_last_answer and user_last_answer.answer:
                                total_primary_score += (user_last_answer.answer.answer_weight * primary_weight_share) / 100

                            # Calculate max score for each bunch activity
                            highest_answer_weight = Answer.objects.filter(activity=bunch_activity).order_by('-answer_weight').first()
                            if highest_answer_weight:
                                total_primary_max_score += (highest_answer_weight.answer_weight * primary_weight_share) / 100

                            # Mark bunch activity as processed
                            processed_activities.add(bunch_activity.id)

            # Calculate performance based on primary evaluatable activities only
            if total_primary_max_score > 0:
                percentage_score = (total_primary_score / total_primary_max_score) * 100
                if percentage_score >= 83.3:
                    phase_scores[phase.name]['high'] += 1
                    phase_scores[phase.name]['high_score'] += percentage_score
                elif percentage_score >= 49.7:
                    phase_scores[phase.name]['mid'] += 1
                    phase_scores[phase.name]['mid_score'] += percentage_score
                else:
                    phase_scores[phase.name]['low'] += 1
                    phase_scores[phase.name]['low_score'] += percentage_score

                phase_scores[phase.name]['total_users'] += 1

    # Prepare response data
    data = {
        'categories': [],
        'low': [],
        'mid': [],
        'high': [],
        'average': []
    }

    for phase in phases:
        data['categories'].append(phase.name)
        total_users = phase_scores[phase.name]['total_users']
        if total_users > 0:
            low_avg = phase_scores[phase.name]['low_score'] / phase_scores[phase.name]['low'] if phase_scores[phase.name]['low'] > 0 else 0
            mid_avg = phase_scores[phase.name]['mid_score'] / phase_scores[phase.name]['mid'] if phase_scores[phase.name]['mid'] > 0 else 0
            high_avg = phase_scores[phase.name]['high_score'] / phase_scores[phase.name]['high'] if phase_scores[phase.name]['high'] > 0 else 0
            overall_avg = (phase_scores[phase.name]['low_score'] + phase_scores[phase.name]['mid_score'] + phase_scores[phase.name]['high_score']) / total_users
        else:
            low_avg = mid_avg = high_avg = overall_avg = 0
        data['low'].append(low_avg)
        data['mid'].append(mid_avg)
        data['high'].append(high_avg)
        data['average'].append(overall_avg)

    return data

@shared_task
def compute_performers_data(scenario_id, group_ids, start_date, end_date):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    start_date = start_date
    end_date = end_date
    
    phases = Phase.objects.filter(scenario=scenario)

    phase_performance = {phase.name: {'low': 0, 'mid': 0, 'high': 0, 'total_users': 0, 'total_score': 0} for phase in phases}

    # Get the last answers (only the latest answer for each user/activity combination)
    last_answers = get_last_answers(scenario_id)

    # Apply start_date and end_date filters
    if start_date:
        start_date = parse_date(start_date)
        if start_date:
            last_answers = last_answers.filter(created_on__gte=start_date)
    
    if end_date:
        end_date = parse_date(end_date)
        if end_date:
            last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))

    # Get the minimum activity ID in the scenario
    min_activity_id = Activity.objects.filter(scenario=scenario).order_by('id').first()

    if not min_activity_id:
        return JsonResponse({"error": "No activities found for this scenario"}, status=400)
    
    if group_ids:
        users = User.objects.filter(
            id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
        ).distinct()# .exclude(groups__name='teachers')
    else:
        users = User.objects.filter(
            Q(userscenarioscore__scenario=scenario) & 
            (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
            ).distinct()
    
    users = users.exclude(groups__name='teachers')
    
    valid_users = []  # List of users who started with the minimum activity

    for user in users:
        # Check if user has answered the minimum activity
        if last_answers.filter(user=user, activity=min_activity_id).exists():
            valid_users.append(user)
    
    for user in valid_users:
        for phase in phases:
            activities_in_phase = Activity.objects.filter(phase=phase)
            total_primary_score = 0
            total_primary_max_score = 0

            # Track processed activities to avoid duplicates
            processed_activities = set()

            # Process primary evaluatable activities (and their bunches)
            primary_evaluatable_activities = activities_in_phase.filter(is_evaluatable=True, is_primary_ev=True)
            primary_count = primary_evaluatable_activities.count()

            if primary_count > 0:
                primary_weight_share = 100 / primary_count  # Each primary activity contributes equally

                for primary_activity in primary_evaluatable_activities:
                    try:
                        question_bunch = QuestionBunch.objects.get(activity_primary=primary_activity)
                        bunch_activities = Activity.objects.filter(id__in=question_bunch.activity_ids)
                    except QuestionBunch.DoesNotExist:
                        bunch_activities = [primary_activity]  # Fallback to primary activity alone

                    for bunch_activity in bunch_activities:
                        if bunch_activity.id not in processed_activities:
                            user_last_answer = last_answers.filter(user=user, activity=bunch_activity).first()
                            if user_last_answer and user_last_answer.answer:
                                total_primary_score += (user_last_answer.answer.answer_weight * primary_weight_share) / 100

                            # Calculate max score for each bunch activity
                            highest_answer_weight = Answer.objects.filter(activity=bunch_activity).order_by('-answer_weight').first()
                            if highest_answer_weight:
                                total_primary_max_score += (highest_answer_weight.answer_weight * primary_weight_share) / 100

                            # Mark bunch activity as processed
                            processed_activities.add(bunch_activity.id)

            # Calculate performance based on primary evaluatable activities
            if total_primary_max_score > 0:
                percentage_score = (total_primary_score / total_primary_max_score) * 100
                if percentage_score >= 83.3:
                    phase_performance[phase.name]['high'] += 1
                elif percentage_score >= 49.7:
                    phase_performance[phase.name]['mid'] += 1
                else:
                    phase_performance[phase.name]['low'] += 1

                # Update total score and user count
                phase_performance[phase.name]['total_score'] += percentage_score
                phase_performance[phase.name]['total_users'] += 1

    # Prepare data for response
    data = {
        'categories': [],
        'low': [],
        'mid': [],
        'high': [],
        'metric': []
    }

    for phase in phases:
        data['categories'].append(phase.name)
        data['low'].append(phase_performance[phase.name]['low'])
        data['mid'].append(phase_performance[phase.name]['mid'])
        data['high'].append(phase_performance[phase.name]['high'])
        if phase_performance[phase.name]['total_users'] > 0:
            average_score = phase_performance[phase.name]['total_score'] / phase_performance[phase.name]['total_users']
        else:
            average_score = 0
        data['metric'].append(average_score)

    return data

@shared_task
def compute_time_spent_by_performer_type(scenario_id, group_ids, start_date, end_date):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    start_date = start_date
    end_date = end_date
    
    phases = Phase.objects.filter(scenario=scenario)

    time_spent = {phase.name: {'low': 0, 'mid': 0, 'high': 0, 'low_count': 0, 'mid_count': 0, 'high_count': 0} for phase in phases}

    # Get the last answers (only the latest answer for each user/activity combination)
    last_answers = get_last_answers(scenario_id)

    # Apply start_date and end_date filters
    if start_date:
        start_date = parse_date(start_date)
        last_answers = last_answers.filter(created_on__gte=start_date)
    
    if end_date:
        end_date = parse_date(end_date)
        last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))

    # Get the minimum activity ID in the scenario
    min_activity_id = Activity.objects.filter(scenario=scenario).order_by('id').first()

    if not min_activity_id:
        return JsonResponse({"error": "No activities found for this scenario"}, status=400)

    # Get all users who answered the minimum activity
    if group_ids:
        users = User.objects.filter(
            id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
        ).distinct()# .exclude(groups__name='teachers')
    else:
        users = User.objects.filter(
            Q(userscenarioscore__scenario=scenario) & 
            (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
            ).distinct()
    
    users = users.exclude(groups__name='teachers')

    valid_users = []  # List of users who started with the minimum activity

    for user in users:
        # Check if user has answered the minimum activity
        if last_answers.filter(user=user, activity=min_activity_id).exists():
            valid_users.append(user)

    # Process only valid users
    for user in valid_users:
        for phase in phases:
            total_time = 0
            total_primary_score = 0
            total_primary_max_score = 0

            processed_activities = set()  # To avoid processing the same activity multiple times

            activities = Activity.objects.filter(phase=phase)

            # Process primary evaluatable activities (and their bunches)
            primary_evaluatable_activities = activities.filter(is_evaluatable=True, is_primary_ev=True)
            primary_count = primary_evaluatable_activities.count()  # Number of primary evaluatable activities

            if primary_count > 0:
                primary_weight_share = 100 / primary_count  # Each primary activity contributes equally

                for primary_activity in primary_evaluatable_activities:
                    try:
                        question_bunch = QuestionBunch.objects.get(activity_primary=primary_activity)
                        bunch_activities = Activity.objects.filter(id__in=question_bunch.activity_ids)
                    except QuestionBunch.DoesNotExist:
                        bunch_activities = [primary_activity]  # Fallback to primary activity alone

                    for bunch_activity in bunch_activities:
                        if bunch_activity.id not in processed_activities:
                            user_last_answer = last_answers.filter(user=user, activity=bunch_activity).first()
                            if user_last_answer:
                                if user_last_answer.answer:
                                    total_primary_score += (user_last_answer.answer.answer_weight * primary_weight_share) / 100
                                if user_last_answer.timing:
                                    total_time += user_last_answer.timing

                            # Calculate max score for each bunch activity
                            highest_answer_weight = Answer.objects.filter(activity=bunch_activity).order_by('-answer_weight').first()
                            if highest_answer_weight:
                                total_primary_max_score += (highest_answer_weight.answer_weight * primary_weight_share) / 100

                            # Mark bunch activity as processed
                            processed_activities.add(bunch_activity.id)

            # Process non-primary evaluatable and non-evaluatable activities
            non_primary_evaluatable_activities = activities.filter(is_evaluatable=True, is_primary_ev=False)
            non_evaluatable_activities = activities.filter(is_evaluatable=False)

            for activity in non_primary_evaluatable_activities.union(non_evaluatable_activities):
                if activity.id not in processed_activities:
                    user_last_answer = last_answers.filter(user=user, activity=activity).first()
                    if user_last_answer:
                        # if user_last_answer.answer:
                        #     total_primary_score += user_last_answer.answer.answer_weight
                        if user_last_answer.timing:
                            total_time += user_last_answer.timing

                    # Mark the activity as processed
                    processed_activities.add(activity.id)

            # Calculate performance based on primary evaluatable activities
            if total_primary_max_score > 0:
                percentage_score = (total_primary_score / total_primary_max_score) * 100
                if percentage_score >= 83.3:
                    time_spent[phase.name]['high'] += total_time
                    time_spent[phase.name]['high_count'] += 1
                elif percentage_score >= 49.7:
                    time_spent[phase.name]['mid'] += total_time
                    time_spent[phase.name]['mid_count'] += 1
                else:
                    time_spent[phase.name]['low'] += total_time
                    time_spent[phase.name]['low_count'] += 1

    # Prepare data for response
    data = {
        'categories': [],
        'low': [],
        'mid': [],
        'high': []
    }

    for phase in phases:
        data['categories'].append(phase.name)
        data['low'].append(time_spent[phase.name]['low'] / time_spent[phase.name]['low_count'] if time_spent[phase.name]['low_count'] > 0 else 0)
        data['mid'].append(time_spent[phase.name]['mid'] / time_spent[phase.name]['mid_count'] if time_spent[phase.name]['mid_count'] > 0 else 0)
        data['high'].append(time_spent[phase.name]['high'] / time_spent[phase.name]['high_count'] if time_spent[phase.name]['high_count'] > 0 else 0)

    return data

@shared_task
def compute_scenario_paths(scenario_id, group_ids, start_date, end_date):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    
    # Parse start and end dates from request
    start_date = start_date
    end_date = end_date

    # Get the first answers (only the earliest answer for each user/activity combination)
    last_answers = get_first_answers(scenario_id)

    # Apply start_date and end_date filters
    if start_date:
        start_date = parse_date(start_date)
        if start_date:
            last_answers = last_answers.filter(created_on__gte=start_date)
    
    if end_date:
        end_date = parse_date(end_date)
        if end_date:
            last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))

    # Get the minimum activity ID in the scenario
    min_activity = Activity.objects.filter(scenario=scenario).order_by('id').first()

    if not min_activity:
        return JsonResponse({"error": "No activities found for this scenario"}, status=400)
    
    # users = User.objects.filter(
    #     Q(userscenarioscore__scenario=scenario) & 
    #     (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
    # ).distinct()
    if group_ids:
        users = User.objects.filter(
            id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
        ).distinct()# .exclude(groups__name='teachers')
    else:
        users = User.objects.filter(
            Q(userscenarioscore__scenario=scenario) & 
            (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
            ).distinct()
    
    users = users.exclude(groups__name='teachers')
    
    valid_users = []  # List of users who started with the minimum activity

    for user in users:
        # Check if user has answered the minimum activity
        if last_answers.filter(user=user, activity=min_activity).exists():
            valid_users.append(user)
    
    scenario_path_counts = defaultdict(lambda: {'count': 0, 'user_ids': []})
    phase_path_counts = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'user_ids': []}))
    all_activity_ids = set() # Mar
    all_phase_ids = set() # Mar

    for user in valid_users:
        user_answers = last_answers.filter(user=user).order_by('created_on')
        
        user_path = []
        phase_paths = defaultdict(list)

        for answer in user_answers:
            aid = answer.activity.id # Mar
            pid = answer.activity.phase.id # Mar
            all_activity_ids.add(aid) # Mar
            all_phase_ids.add(pid) # Mar

            activity_id = answer.activity.id
            phase = answer.activity.phase
            
            user_path.append(activity_id)
            phase_paths[phase.id].append(activity_id)

        path_tuple = tuple(user_path)
        if path_tuple:
            scenario_path_counts[path_tuple]['count'] += 1
            scenario_path_counts[path_tuple]['user_ids'].append(user.id)

        #for phase_id, phase_path in phase_paths.items():
        for pid, phase_path in phase_paths.items(): # Mar
            phase_path_tuple = tuple(phase_path)
            if phase_path_tuple:
                phase_path_counts[pid][phase_path_tuple]['count'] += 1 # phase_id
                phase_path_counts[pid][phase_path_tuple]['user_ids'].append(user.id) # phase_id
    
    # Fetch all activities and phases in bulk
    activities = Activity.objects.filter(id__in=all_activity_ids)
    activity_dict = {a.id: a for a in activities}
    phases = Phase.objects.filter(id__in=all_phase_ids)
    phase_dict = {p.id: p for p in phases}

    # Helper function to serialize activity
    def serialize_activity(aid):
        activity = activity_dict[aid]
        return {
            'id': activity.id,
            'name': activity.name,
            'short_name': (activity.name[:20] + '...') if len(activity.name) > 20 else activity.name,
            'tooltip': f"{activity.name} | {activity.phase.name if activity.phase else 'Unknown Phase'}",
            'url': f'/authoringtool/scenarios/{activity.scenario.id}/viewPhase/{activity.phase.id}/viewActivity/{activity.id}/'
        }

    max_scenario_count = max((data['count'] for data in scenario_path_counts.values()), default=0) # max_count
    # common_scenario_paths = [path for path, data in scenario_path_counts.items() if data['count'] == max_count]
    common_scenario_path = next((path for path, data in scenario_path_counts.items() if data['count'] == max_scenario_count), None)
    unique_scenario_path_count = len(scenario_path_counts)

    most_common_scenario = [serialize_activity(aid) for aid in common_scenario_path] if common_scenario_path else []

    # All scenario paths for modal
    all_scenario_paths = [
        {
            'path': [serialize_activity(aid) for aid in path],
            'count': data['count'],
            'user_ids': data['user_ids']
        }
        for path, data in scenario_path_counts.items()
    ]

    phase_path_data = {}

    for pid, paths in phase_path_counts.items():
        max_phase_count = max((data['count'] for data in paths.values()), default=0)
        common_phase_path = next((path for path, data in paths.items() if data['count'] == max_phase_count), None)
        unique_phase_path_count = len(paths)
        phase = phase_dict.get(pid)

        most_common_phase = [serialize_activity(aid) for aid in common_phase_path] if common_phase_path else []

        # All paths for modal
        all_phase_paths = [
            {
                'path': [serialize_activity(aid) for aid in path],
                'count': data['count'],
                'user_ids': data['user_ids']
            }
            for path, data in paths.items()
        ]

        # Final phase data
        phase_path_data[pid] = {
            'phase_name': phase.name if phase else "Unknown Phase",
            'most_common_path': {
                'path': most_common_phase,
                'count': max_phase_count
            },
            'paths': all_phase_paths,
            'unique_path_count': unique_phase_path_count
        }

    # Final structured return (Celery-friendly)
    return {
        'scenario_paths': {
            'most_common_path': {
                'path': most_common_scenario,
                'count': max_scenario_count
            },
            'paths': all_scenario_paths,
            'unique_path_count': unique_scenario_path_count
        },
        'phase_paths': phase_path_data
    }

@shared_task
def compute_student_performance_metrics(scenario_id, group_ids, start_date, end_date):
    scenario = get_object_or_404(Scenario, id=scenario_id)
    phases = Phase.objects.filter(scenario=scenario).order_by('id')
    
    # Define the weights for each phase in the order they come
    phase_weights = [0.2, 0.2, 0.45, 0.15]
    if len(phases) > 4:
        phase_weights = [0.2, 0.2, 0.3, 0.15, 0.15]

    # Get the last answers (only the latest answer for each user/activity combination)
    last_answers = get_last_answers(scenario_id)

    if group_ids:
        users = User.objects.filter(
            id__in=UserGroupMembership.objects.filter(group_id__in=group_ids).values_list('user_id', flat=True)
        ).distinct()# .exclude(groups__name='teachers')
    else:
        users = User.objects.filter(
            Q(userscenarioscore__scenario=scenario) & 
            (Q(school_department__isnull=False) | Q(id__in=UserGroupMembership.objects.values('user_id')))
            ).distinct()
    
    users = users.exclude(groups__name='teachers')

    # Get the minimum activity ID in the scenario
    min_activity = Activity.objects.filter(scenario=scenario).order_by('id').first()

    if not min_activity:
        return JsonResponse({"error": "No activities found for this scenario"}, status=400)

    valid_users = []  # List of users who started with the minimum activity

    for user in users:
        # Check if user has answered the minimum activity
        if last_answers.filter(user=user, activity=min_activity).exists():
            valid_users.append(user)
    
    # Apply start_date and end_date filters
    if start_date:
        start_date = parse_date(start_date)
        if start_date:
            last_answers = last_answers.filter(created_on__gte=start_date)
    
    if end_date:
        end_date = parse_date(end_date)
        if end_date:
            last_answers = last_answers.filter(created_on__lte=end_date + timedelta(days=1))

    csv_data = []
    for user in valid_users:
        row = [user.id, user.username, scenario.name]
        total_weighted_score = 0
        total_max_weighted_score = 0

        for index, phase in enumerate(phases):
            phase_total_time = 0
            phase_total_score = 0
            phase_max_score = 0
            processed_activities = set()

            # Fetch all activities in the phase
            all_activities = Activity.objects.filter(phase=phase)
            for activity in all_activities:
                # Sum up the timings for all activities
                user_last_answer = last_answers.filter(user=user, activity=activity).first()
                if user_last_answer and user_last_answer.timing:
                    phase_total_time += user_last_answer.timing

            # Handle primary evaluatable activities
            primary_evaluatable_activities = Activity.objects.filter(phase=phase, is_evaluatable=True, is_primary_ev=True)
            for primary_activity in primary_evaluatable_activities:
                try:
                    question_bunch = QuestionBunch.objects.get(activity_primary=primary_activity)
                    bunch_activities = Activity.objects.filter(id__in=question_bunch.activity_ids)
                except QuestionBunch.DoesNotExist:
                    bunch_activities = [primary_activity]

                for bunch_activity in bunch_activities:
                    if bunch_activity.id not in processed_activities:
                        user_last_answer = last_answers.filter(user=user, activity=bunch_activity).first()
                        if user_last_answer and user_last_answer.answer:
                            phase_total_score += user_last_answer.answer.answer_weight

                        # Calculate max score for the activity
                        highest_answer_weight = Answer.objects.filter(activity=bunch_activity).order_by('-answer_weight').first()
                        if highest_answer_weight:
                            phase_max_score += highest_answer_weight.answer_weight

                        processed_activities.add(bunch_activity.id)

            # Calculate weighted scores for the phase
            if phase_max_score > 0:
                weight = phase_weights[index]
                weighted_score = (phase_total_score / phase_max_score) * weight
                max_weighted_score = weight
                total_weighted_score += weighted_score
                total_max_weighted_score += max_weighted_score

            # Categorize phase performance
            phase_percentage = (phase_total_score / phase_max_score) * 100 if phase_max_score > 0 else 0
            if phase_percentage >= 83.3:
                phase_categorization = 'High'
            elif phase_percentage >= 49.7:
                phase_categorization = 'Moderate'
            else:
                phase_categorization = 'Low'

            # Add phase data to row
            row.extend([phase_categorization, phase_total_time, phase_total_score])

        # Final categorization
        final_percentage = (total_weighted_score / total_max_weighted_score) * 100 if total_max_weighted_score > 0 else 0
        if final_percentage >= 83.3:
            final_categorization = 'High'
        elif final_percentage >= 49.7:
            final_categorization = 'Moderate'
        else:
            final_categorization = 'Low'

        # Add final categorization to row
        row.append(final_categorization)
        csv_data.append(row)

    # Generate CSV content
    csv_buffer = io.StringIO()
    csv_writer = csv.writer(csv_buffer)
    header = ['User ID', 'Username', 'Scenario Name']
    for phase in phases:
        header.extend([f"{phase.name} Categorization", f"{phase.name} Time", f"{phase.name} Score"])
    header.append('Final Categorization')
    csv_writer.writerow(header)
    csv_writer.writerows(csv_data)
    csv_buffer.seek(0)

    return {
        "csv_content": csv_buffer.getvalue(),  # Include CSV content as a string
        "message": "Student performance metrics computed successfully."
    }