
import mlflow
from sklearn import datasets
from sklearn import metrics
import requests
import json
import ast
import os
import numpy as np 
import pandas as pd 
from sklearn.model_selection import train_test_split
from hyperopt import STATUS_OK, Trials, fmin, hp, tpe
from xgboost import XGBClassifier
from sklearn.metrics import auc ,accuracy_score ,roc_curve, roc_auc_score , f1_score
from mlflow.tracking import MlflowClient
from mlflow.entities import ViewType
from mlflow.store.artifact.runs_artifact_repo import RunsArtifactRepository
from prefect.task_runners import SequentialTaskRunner
from prefect.deployments import Deployment
from prefect import flow , task
from prefect.server.schemas.schedules import CronSchedule


@task
def mlflow_environment(experiment_name):
    mlflow.set_tracking_uri("http://127.0.0.1:5000") #  connects to a tracking URI.
    mlflow.set_experiment(experiment_name)
    experiment = mlflow.get_experiment_by_name(experiment_name)
    
    return experiment.experiment_id

@task
def load_data():
    digits = datasets.load_digits() #dataset loading
    x = digits.data               #Features stored in X 
    y = digits.target 
    df = pd.DataFrame(data= np.c_[digits['data'], digits['target']],
                     columns= digits['feature_names'] + ['target'])
    
    return df


@task
def split_data(df):
    x_train, x_test, y_train, y_test = train_test_split(df[df.columns[:-1]], df[df.columns[-1]], test_size=0.2, random_state=42)
    return x_train, x_test, y_train, y_test



@task
def train_hyperparameter_tuning(x_train, x_test, y_train, y_test, model_name):
    search_space=space = {
    'learning_rate': hp.choice('learning_rate', [0.0005,0.001, 0.01, 0.5, 1]),
    'max_depth' : hp.choice('max_depth', range(3,21,3)),
    'gamma' : hp.choice('gamma', [i/10.0 for i in range(0,5)]),
    'colsample_bytree' : hp.choice('colsample_bytree', [i/10.0 for i in range(3,10)]),     
    'reg_alpha' : hp.choice('reg_alpha', [1e-5, 1e-2, 0.1, 1, 10, 100]), 
    'reg_lambda' : hp.choice('reg_lambda', [1e-5, 1e-2, 0.1, 1, 10, 100]),
    'seed': hp.choice('seed', [0,7,42])
}
    

    def objective (params):
    
        with mlflow.start_run():
            mlflow.set_tag("model", "xgboost")
            mlflow.log_params(params)
            clf=XGBClassifier(**params)
            evaluation = [( x_train, y_train), (x_test, y_test)]
            clf.fit(x_train, y_train,
                    eval_set=evaluation, early_stopping_rounds=10, verbose=False)
            y_pred=clf.predict(x_test)
            y_score=clf.predict_proba(x_test)
            accuracy=accuracy_score(y_test,y_pred)
            mlflow.log_metric("accuracy", accuracy)
            f1= f1_score(y_test,y_pred,  average='micro')
            mlflow.log_metric("f1_score", f1)
            mlflow.xgboost.log_model(
            xgb_model=clf,
            artifact_path="mlruns",
            registered_model_name=model_name,
            
                
        )

        return {'loss': -accuracy, 'status': STATUS_OK } 

    
    best_result = fmin(
    fn=objective,
    space=search_space,
    algo=tpe.suggest,
    max_evals=5,
    trials=Trials())

    return best_result

@task
def get_best_model(experiment_id):
    client = MlflowClient(tracking_uri="http://127.0.0.1:5000")
    run = MlflowClient().search_runs(
    experiment_ids=experiment_id,
    run_view_type=ViewType.ACTIVE_ONLY,
    order_by=["metrics.accuracy DESC"]
    )[0]
    run_id = run.info.run_id
    model_uri = f"runs:/{run_id}/mlruns"
    model_src = RunsArtifactRepository.get_underlying_uri(model_uri)

    filter_string = "run_id='{}'".format(run_id)
    results = client.search_model_versions(filter_string)
    model_version=results[0].version

    return model_version ,model_uri 

@task
def promote_best_model(model_version,model_name):
    new_stage = "Production"
    client = MlflowClient(tracking_uri="http://127.0.0.1:5000")
    client.transition_model_version_stage(
            name=model_name,
            version=model_version,
            stage=new_stage,
            archive_existing_versions=False
    )
    
@task
def serve_model(model_uri):
   os.system("start killport 8080 ")
   os.chdir("D:\DEV\Prefect")
   os.environ["MLFLOW_TRACKING_URI"] = "http://127.0.0.1:5000"
   os.system(f"start mlflow models serve --model-uri  {model_uri}  -p 8080 --no-conda ") 





@flow
def main(experiment_name, model_name):
    experiment_id=mlflow_environment(experiment_name)
    df=load_data()
    x_train, x_test, y_train, y_test=split_data(df)
    best_result=train_hyperparameter_tuning(x_train, x_test, y_train, y_test,model_name)
    model_version,model_uri=get_best_model(experiment_id)
    print('model_uri :',model_uri)
    promote_best_model(model_version,model_name)
    serve_model(model_uri)



if __name__ == "__main__":

    deployment = Deployment.build_from_flow(
        flow=main,
        name="model_training_and_tuning_weekly",
        parameters={'experiment_name':'digits_experiment',
                    'model_name':'xgboost',
                   },
        # At 12:00 AM, only on Thursday
        schedule=CronSchedule(cron="00 00 * * 4", timezone="Africa/Cairo"),
        version=1,
        work_queue_name="ml",
    )

    deployment.apply()

   



