from django.db import models
from django.contrib.auth.models import User
from django.contrib.postgres.fields import IntegerRangeField, ArrayField
from organization.models import Organization
import qrcode
from io import BytesIO
from django.core.files import File

class Scenario(models.Model):
    VISIBILITY_CHOICES = [
        ('private', 'Private (In-Progress)'),
        ('org', 'Organization Users Only'),
        ('public', 'Public'),
    ]
    name = models.CharField(max_length=255, unique=True)
    learning_goals = models.TextField(blank=True)
    description = models.TextField(blank=True)
    age_of_students = IntegerRangeField(blank=True, null=True)
    subject_domains = models.CharField(max_length=255, blank=True)
    language = models.CharField(max_length=255, blank=True)
    suggested_learning_time = models.IntegerField(null=True)
    image = models.ImageField(upload_to='images', null=True, blank=True)
    video_url = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, related_name='created_scenarios')
    created_on = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, related_name='updated_scenarios')
    updated_on = models.DateTimeField(auto_now=True)

    # New field to manage visibility status
    visibility_status = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='private')

    # Add an optional link to the organization (if needed)
    organizations = models.ManyToManyField(Organization, blank=True, related_name='scenarios')  # Allow multiple organizations

    # Editable by organization's members
    is_editable_by_org = models.BooleanField(default=False, help_text="If checked, members of the selected organization(s) can edit this scenario.")

    class Meta:
        verbose_name = 'Scenario'
        verbose_name_plural = 'Scenarios'
        ordering = ['created_on']

    def __str__(self):
        return self.name

class Phase(models.Model):
    name = models.CharField(max_length=255, null=False)
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='images', null=True, blank=True)
    video_url = models.TextField(blank=True, null=True)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='phases')
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, related_name='created_phases')
    created_on = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, related_name='updated_phases')
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Phase'
        verbose_name_plural = 'Phases'
        ordering = ['created_on']

    def __str__(self):
        return self.name

class ActivityType(models.Model):
    name = models.CharField(max_length=255, null=False, blank=False)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, related_name='created_activity_types')
    created_on = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, related_name='updated_activity_types')
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Activity Type"
        verbose_name_plural = "Activity Types"
        ordering = ['created_on']

    def __str__(self):
        return self.name

class Simulation(models.Model):
    name = models.CharField(max_length=200)
    iframe_url = models.URLField()
    width = models.PositiveIntegerField(default=800)
    height = models.PositiveIntegerField(default=600)
    allow_fullscreen = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Simulation"
        verbose_name_plural = "Simulations"
        ordering = ['name']

    def __str__(self):
        return self.name

# LabsLand Integration
class ExperimentLL(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField()
    launch_url = models.URLField()
    consumer_key = models.CharField(max_length=100)
    shared_secret = models.CharField(max_length=100)
    picture = models.ImageField(upload_to='experiment_pictures/', blank=True, null=True)

    class Meta:
        verbose_name = "LabsLand Experiment"
        verbose_name_plural = "LabsLand Experiments"
        ordering = ["id"]

    def __str__(self):
        return self.name

# VR/AR Integration QR CODE - 31/03
class VRARExperiment(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField()
    launch_url = models.URLField()
    qr_code = models.ImageField(upload_to='qr_codes/', blank=True, null=True)  # Add QR code field
    picture = models.ImageField(upload_to='vr_ar_experiment_pictures/', blank=True, null=True)

    class Meta:
        verbose_name = "VR/AR Experiment"
        verbose_name_plural = "VR/AR Experiments"
        ordering = ['id']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Generate QR code only if the URL is present and QR code does not already exist
        if self.launch_url and not self.qr_code:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(self.launch_url)
            qr.make(fit=True)

            img = qr.make_image(fill='black', back_color='white')

            # Save the QR code to a file
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            filename = f'{self.name}_qr.png'
            self.qr_code.save(filename, File(buffer), save=False)
        
        super().save(*args, **kwargs)

class Activity(models.Model):
    name = models.CharField(max_length=255, null=False, blank=False, default='ActivityDefaultName')
    text = models.TextField(null=False, blank=False)
    plain_text = models.TextField(blank=True)
    correct_count = models.IntegerField(default=0)
    incorrect_count = models.IntegerField(default=0)
    is_evaluatable = models.BooleanField(default=False)
    is_primary_ev = models.BooleanField(default=False)
    must_wait = models.BooleanField(default=False)
    score_limit = models.FloatField(default=0.0)
    scenario = models.ForeignKey('Scenario', on_delete=models.CASCADE, null=True, related_name='activities')
    phase = models.ForeignKey('Phase', on_delete=models.CASCADE, null=True, related_name='activities')
    activity_type = models.ForeignKey('ActivityType', on_delete=models.SET_NULL, null=True, related_name='activities')
    helper = models.CharField(max_length=255, blank=True)
    simulation = models.ForeignKey(Simulation, on_delete=models.SET_NULL, null=True, blank=True)
    experiment_ll = models.ForeignKey(ExperimentLL, on_delete=models.SET_NULL, null=True, blank=True)  # LabsLand Integration
    vr_ar_experiment = models.ForeignKey('VRARExperiment', on_delete=models.SET_NULL, null=True, blank=True) # VR_AR
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, related_name='created_activities')
    created_on = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, related_name='updated_activities')
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Activity"
        verbose_name_plural = "Activities"
        ordering = ['created_on']

    def __str__(self):
        return f"{self.name} Scenario {self.scenario.id}"
    

class Answer(models.Model):
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='answers')
    text = models.TextField(null=False, blank=False)
    is_correct = models.BooleanField(default=False)
    answer_weight = models.IntegerField(default=0, blank=True)
    image = models.ImageField(upload_to='images', null=True, blank=True)
    vid_url = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, null=True, related_name='created_answers')
    created_on = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, null=True, related_name='updated_answers')
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Answer"
        verbose_name_plural = "Answers"
        ordering = ['created_on']
    
    def __str__(self):
        return self.text
    
class AnswerFeedback(models.Model):
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name='feedbacks')
    text = models.TextField(null=True, blank=True)
    image= models.ImageField(upload_to='images', null=True, blank=True)
    vid_url = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, null=True, related_name='created_answer_feedbacks')
    created_on = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, null=True, related_name='updated_answer_feedbacks')
    updated_on = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Feedback"
        verbose_name_plural = "Feedbacks"
        ordering = ['created_on']

    def __str__(self):
        return self.feedback_text
    
class NextQuestionLogic(models.Model):
    activity = models.ForeignKey(Activity, related_name='next_logic', on_delete=models.CASCADE)
    answer = models.ForeignKey(Answer, related_name='next_logic', on_delete=models.CASCADE, null=True, blank=True)
    next_activity = models.ForeignKey(Activity, related_name='previous_logic', on_delete=models.CASCADE, null=True)

    class Meta:
        unique_together = ('activity', 'answer')  # Enforces the unique constraint for question-answer pairs
        verbose_name = "Next Question"
        verbose_name_plural = "Next Questions"
        ordering = ["activity"]
    
    def __str__(self):
        return f"Activity {self.activity.id} to Next Activity {self.next_activity.id}"
    
class QuestionBunch(models.Model):
    activity_ids = ArrayField(models.IntegerField(), blank=False)
    activity_primary = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='question_bunches')

    class Meta:
        verbose_name = "Activity Bunch"
        verbose_name_plural = "Activity Bunches"
        ordering = ["activity_ids"]

    def __str__(self):
        return f"Bunch {self.pk}"

class EvQuestionBranching(models.Model):
    activity = models.OneToOneField(Activity, on_delete=models.CASCADE, primary_key=True, related_name='branching')
    next_question_on_high = models.ForeignKey(Activity, on_delete=models.SET_NULL, null=True, blank=True, related_name='next_high')
    next_question_on_high_feedback = models.TextField(blank=True)
    next_question_on_mid = models.ForeignKey(Activity, on_delete=models.SET_NULL, null=True, blank=True, related_name='next_mid')
    next_question_on_mid_feedback = models.TextField(blank=True)
    next_question_on_low = models.ForeignKey(Activity, on_delete=models.SET_NULL, null=True, blank=True, related_name='next_low')
    next_question_on_low_feedback = models.TextField(blank=True)

    class Meta:
        verbose_name = "Evaluating Question Branching"
        verbose_name_plural = "Evaluating Question Branching"
        ordering = ["activity"]

    def __str__(self):
        return f"Branching for Activity {self.activity}"
    
class UserScenarioScore(models.Model):
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE)
    user_score = models.IntegerField(default=0)
    last_activity = models.ForeignKey(Activity, on_delete=models.SET_NULL, null=True, blank=True)
    timeDoingScenario = models.IntegerField(default=0, blank=True, null=True)

    class Meta:
        verbose_name = "User & Scenario Score"
        verbose_name_plural = "User & Scenario Scores"
        ordering = ["user"]

    def __str__(self):
        return f"{self.user.username} - {self.scenario.name} Score: {self.user_score} Last Activity Answered: {self.last_activity.name}"

class UserAnswer(models.Model):
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE)
    answer = models.ForeignKey(Answer, on_delete=models.SET_NULL, null=True, blank=True)
    timing = models.IntegerField(default=0, blank=True, null=True)
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User's Answer"
        verbose_name_plural = "User's Answers"
        ordering = ["user"]

    def __str__(self):
        return f"{self.user.username} - {self.activity.name}"
        
class SchoolDepartment(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name = "School Department"
        verbose_name_plural = "School Departments"
        ordering = ["id"]

    def __str__(self):
        return self.name
        
class PhetLabSessions(models.Model):
    name = models.CharField(max_length=255, null=False, blank=False, default='Phet Default')
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    activity = models.ForeignKey(Activity, on_delete=models.DO_NOTHING)
    mass_1 = models.FloatField(null=True, blank=True)
    mass_2 = models.FloatField(null=True, blank=True)
    length_1 = models.FloatField(null=True, blank=True)
    length_2 = models.FloatField(null=True, blank=True)
    angle_1 = models.FloatField(null=True, blank=True)
    angle_2 = models.FloatField(null=True, blank=True)
    gravity = models.FloatField()
    friction = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Phet Lab Session"
        verbose_name_plural = "Phet Lab Sessions"
        ordering = ["user"]
    
    def __str__(self):
        return f"{self.name} - {self.user.id} - Activity {self.activity}"

class RemoteLabSession(models.Model):
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='lab_sessions')  # Adjusted to refer to Activity instead of Question
    phase = models.ForeignKey(Phase, on_delete=models.CASCADE, related_name='lab_sessions')
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='lab_sessions')
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    start = models.DateTimeField()
    end = models.DateTimeField()
    lab_name = models.CharField(max_length=255)
    pre_duration = models.DurationField()
    exec_duration = models.DurationField()
    mass = models.CharField(max_length=255)
    angle = models.IntegerField()
    iteration = models.IntegerField()

    class Meta:
        verbose_name = "LabsLand Lab Session"
        verbose_name_plural = "LabsLand Lab Sessions"
        ordering = ["id"]

class MultilingualQuestion(models.Model):
    LANGUAGES = [
        ('en', 'English'),
        ('pt', 'Portuguese'),
        ('gr', 'Greek'),
        ('es', 'Spanish'),
        ('fr', 'French'),
        ('de', 'German'),
    ]
    
    # Remove the scenario foreign key since questions will be common
    question_text_en = models.TextField(verbose_name='Question (English)')
    question_text_pt = models.TextField(verbose_name='Question (Portuguese)', blank=True)
    question_text_gr = models.TextField(verbose_name='Question (Greek)', blank=True)
    question_text_es = models.TextField(verbose_name='Question (Spanish)', blank=True)
    question_text_fr = models.TextField(verbose_name='Question (French)', blank=True)
    question_text_de = models.TextField(verbose_name='Question (German)', blank=True)
    is_required = models.BooleanField(default=False)
    order = models.IntegerField(default=0)
    created_on = models.DateTimeField(auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, null=True, related_name='created_multilingual_questions')
    updated_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, null=True, related_name='updated_multilingual_questions')

    class Meta:
        verbose_name = "Multilingual Question"
        verbose_name_plural = "Multilingual Questions"
        ordering = ['order', 'created_on']

    def __str__(self):
        return f"Question: {self.question_text_en[:50]}..."

class MultilingualAnswer(models.Model):
    question = models.ForeignKey(MultilingualQuestion, on_delete=models.CASCADE, related_name='answers')
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name='question_answers', null=True, blank=True)
    answer_text = models.TextField()
    created_on = models.DateTimeField(auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, null=True, related_name='created_multilingual_answers')
    updated_by = models.ForeignKey('auth.User', on_delete=models.SET_DEFAULT, default=1, null=True, related_name='updated_multilingual_answers')

    class Meta:
        verbose_name = "Multilingual Answer"
        verbose_name_plural = "Multilingual Answers"
        ordering = ['-created_on']
        unique_together = ['question', 'user', 'scenario']

    def __str__(self):
        return f"Answer by {self.user.username} for question {self.question.id} in scenario {self.scenario.name}"

User.add_to_class('school_department', models.ForeignKey(SchoolDepartment, on_delete=models.SET_NULL, null=True, blank=True))